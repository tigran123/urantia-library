#!/usr/bin/env python3

import os
import sys
import hashlib
import sqlite3
import re
import shutil
import subprocess
import fnmatch
import ast
from argparse import ArgumentParser
from datetime import datetime, timezone

CHUNK_SIZE = 8 * 1024 * 1024  # 8 MiB

# Newly ingested books start at the highest clearance so they are only visible
# to admins until a librarian has reviewed their metadata and lowered the gate.
DEFAULT_BOOK_CLEARANCE = 100

# Hardcoded safety net applied in addition to (or in absence of) exclude.txt.
# .data and urantia-library MUST always be skipped to avoid migrating the
# vault or the webapp source back into itself.
DEFAULT_DIR_EXCLUDES = {'.data', 'urantia-library', 'Html-Docs', 'Subjects'}
DEFAULT_FILE_EXCLUDES = {'.htaccess', 'md5sums.txt', 'CLAUDE.md', 'GEMINI.md', 'exclude.txt'}


# ---------- Metadata extractors ----------

def extract_djvu_meta(filepath):
    """Fast extraction for DjVu files using djvused, with octal UTF-8 decoding."""
    meta = {}
    try:
        result = subprocess.run(['djvused', '-e', 'print-meta', filepath], capture_output=True, text=True, errors='replace', timeout=10)
        if '\ufffd' in result.stdout:
            print(f"      [!] Warning: Non-UTF8 characters detected and replaced in djvused output for {os.path.basename(filepath)}")
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                parts = line.split('\t', 1)
                if len(parts) == 2:
                    key = parts[0].strip().lower()
                    raw_val = parts[1].strip()
                    try:
                        val_bytes = ast.literal_eval('b' + raw_val)
                        val = val_bytes.decode('utf-8').strip()
                    except Exception as e:
                        print(f"      [!] UTF-8 decoding failed for djvused metadata value in {os.path.basename(filepath)}: {e}")
                        val = raw_val.strip('" ')
                    if val:
                        if key == 'title': meta['title'] = val
                        elif key == 'author': meta['author'] = val
                        elif key == 'publisher': meta['publisher'] = val
                        elif key == 'year': meta['published'] = val
                        elif key == 'keywords': meta['tags'] = val
                        elif key == 'descr': meta['annotation'] = val
                        elif key == 'isbn':
                            meta['identifiers'] = f"isbn:{val}" if not val.startswith('isbn:') else val
                        elif key == 'lang': meta['languages'] = val
    except Exception as e:
        print(f"      [!] djvused failed for {os.path.basename(filepath)}: {e}")
    return meta


def parse_htaccess_line(line):
    """Parses .htaccess, ignores entry if description is just the filename."""
    match = re.search(r'AddDescription\s+"([^"]+)"\s+(.+)', line)
    if not match:
        return None
    metadata_str, filename = match.groups()
    filename = filename.strip().strip('"\'')
    metadata_str = metadata_str.strip()
    if metadata_str == filename:
        return None
    meta = {'title': None, 'author': None, 'annotation': metadata_str}
    title_match = re.search(r"'([^']+)'", metadata_str)
    if title_match:
        meta['title'] = title_match.group(1)
        author_part = metadata_str.split(f"'{meta['title']}'")[0].strip(', ')
        if author_part:
            meta['author'] = author_part
    return filename, meta


def extract_pdfinfo(filepath):
    """Fast extraction specifically for PDF files."""
    meta = {}
    try:
        result = subprocess.run(['pdfinfo', filepath], capture_output=True, text=True, errors='replace', timeout=10)
        if '\ufffd' in result.stdout:
            print(f"      [!] Warning: Non-UTF8 characters detected and replaced in pdfinfo output for {os.path.basename(filepath)}")
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith('Title:'):
                    val = line.split(':', 1)[1].strip()
                    if val: meta['title'] = val
                elif line.startswith('Author:'):
                    val = line.split(':', 1)[1].strip()
                    if val: meta['author'] = val
    except Exception as e:
        print(f"      [!] pdfinfo failed for {os.path.basename(filepath)}: {e}")
    return meta


def extract_ebook_meta(filepath):
    """Rich extraction for EPUB, FB2, MOBI, etc."""
    meta = {}
    try:
        result = subprocess.run(['ebook-meta', filepath], capture_output=True, text=True, errors='replace', timeout=15)
        if '\ufffd' in result.stdout:
            print(f"      [!] Warning: Non-UTF8 characters detected and replaced in ebook-meta output for {os.path.basename(filepath)}")
        if result.returncode == 0:
            lines = result.stdout.splitlines()
            current_key = None
            comments_buffer = []
            for line in lines:
                match = re.match(r'^([A-Za-z\(\)]+)\s*:\s*(.*)', line)
                if match:
                    raw_key, val = match.groups()
                    key = raw_key.strip().lower()
                    val = val.strip()
                    # ebook-meta prints "Unknown" as a sentinel when it can't
                    # read the field (e.g. when given a file with no extension
                    # so it can't dispatch a format reader). Treat it as empty.
                    if val == 'Unknown':
                        val = ''
                    if key == 'title' and val: meta['title'] = val
                    elif key == 'author(s)' and val: meta['author'] = re.sub(r'\[.*?\]', '', val).strip()
                    elif key == 'publisher' and val: meta['publisher'] = val
                    elif key == 'tags' and val: meta['tags'] = val
                    elif key == 'series' and val: meta['series'] = val
                    elif key == 'languages' and val: meta['languages'] = val
                    elif key == 'published' and val: meta['published'] = val
                    elif key == 'identifiers' and val: meta['identifiers'] = val
                    elif key == 'comments':
                        current_key = 'comments'
                        if val: comments_buffer.append(val)
                    else:
                        current_key = None
                elif current_key == 'comments':
                    comments_buffer.append(line.strip())
            if comments_buffer:
                meta['annotation'] = '\n'.join(comments_buffer).strip()
    except Exception as e:
        print(f"      [!] ebook-meta failed for {os.path.basename(filepath)}: {e}")
    return meta


# ---------- CAS helpers ----------




def copy_and_hash(src_path, vault_dir):
    """Stream src_path into vault_dir/<hash> via a temp file, hashing in the
    same pass. Returns the hex digest. If the digest already exists in the
    vault, the temp file is discarded (dedup). The destination is renamed
    atomically only after the full content is written."""
    hasher = hashlib.blake2b()
    os.makedirs(vault_dir, exist_ok=True)
    tmp = os.path.join(vault_dir, f".tmp.{os.getpid()}.{hashlib.sha1(os.fsencode(src_path)).hexdigest()[:16]}")
    try:
        with open(src_path, 'rb') as fin, open(tmp, 'wb') as fout:
            while chunk := fin.read(CHUNK_SIZE):
                hasher.update(chunk)
                fout.write(chunk)
        digest = hasher.hexdigest()
        final = os.path.join(vault_dir, digest)
        if os.path.exists(final):
            os.remove(tmp)
        else:
            os.replace(tmp, final)
            try:
                shutil.copystat(src_path, final)
            except OSError:
                pass
        return digest
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


# ---------- Exclude rules ----------

def load_excludes(path):
    """Parse an rsync-ish exclude file. Returns (dir_patterns, any_patterns)
    or (None, None) if the file is missing. Lines ending in '/' match
    directories only; other lines match either dir or file names."""
    if not path or not os.path.exists(path):
        return None, None
    dir_pats, any_pats = [], []
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            pat = line.strip()
            if not pat or pat.startswith('#'):
                continue
            if pat.endswith('/'):
                dir_pats.append(pat.rstrip('/'))
            else:
                any_pats.append(pat)
    return dir_pats, any_pats


def matches(name, relpath, patterns):
    for p in patterns:
        if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(relpath, p):
            return True
    return False


# ---------- Main ----------

def main():
    p = ArgumentParser(description="CAS migration: ingest books from --src into --target/.data, write metadata to --db.")
    p.add_argument("--src", required=True,
                   help="Source root, walked recursively.")
    p.add_argument("--target", required=True,
                   help="Target root. The .data/ vault and the symlink tree mirroring --src live here.")
    p.add_argument("--db", required=True,
                   help="Path to the SQLite database (schema must already be applied; see initdb.sh).")
    p.add_argument("--exclude-file", default=None,
                   help="Override exclude list path. Defaults to <src>/exclude.txt if that file exists.")
    args = p.parse_args()

    src_root = os.path.abspath(args.src)
    target_root = os.path.abspath(args.target)

    if src_root == target_root:
        print("[!] ERROR: Source and target directories must be different (in-place migration is disabled).", file=sys.stderr)
        sys.exit(1)

    vault_dir = os.path.join(target_root, ".data")
    covers_vault = os.path.join(vault_dir, "covers")
    os.makedirs(covers_vault, exist_ok=True)

    excludes_path = args.exclude_file if args.exclude_file else os.path.join(src_root, "exclude.txt")
    dir_pats, any_pats = load_excludes(excludes_path)
    if dir_pats is None:
        dir_pats, any_pats = [], []
        excl_summary = "no exclude file (using hardcoded defaults only)"
    else:
        excl_summary = f"loaded {excludes_path} ({len(dir_pats)} dir-only, {len(any_pats)} general)"

    if not os.path.exists(args.db):
        print(f"[!] DB not found: {args.db}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(args.db)
    cursor = conn.cursor()

    print("Mode:     COPY (src is read-only)")
    print(f"Source:   {src_root}")
    print(f"Target:   {target_root}")
    print(f"Vault:    {vault_dir}")
    print(f"DB:       {args.db}")
    print(f"Excludes: {excl_summary}")

    n_processed, n_skipped, n_errors = 0, 0, 0

    for root, dirs, files in os.walk(src_root, followlinks=False):
        rel_root = os.path.relpath(root, src_root)
        if rel_root == ".":
            rel_root = ""

        # Filter dirs before descent. Mutate `dirs` in place per os.walk contract.
        kept_dirs = []
        for d in dirs:
            if d.startswith('.'):
                continue
            if d in DEFAULT_DIR_EXCLUDES:
                continue
            rel = os.path.join(rel_root, d) if rel_root else d
            if matches(d, rel, dir_pats) or matches(d, rel, any_pats):
                continue
            kept_dirs.append(d)
        dirs[:] = kept_dirs

        # Read .htaccess metadata for this dir (never modified).
        htaccess_data = {}
        htaccess_path = os.path.join(root, ".htaccess")
        if os.path.exists(htaccess_path):
            try:
                with open(htaccess_path, 'r', encoding='utf-8', errors='replace') as f:
                    for line in f:
                        if line.startswith("AddDescription"):
                            parsed = parse_htaccess_line(line)
                            if parsed:
                                htaccess_data[parsed[0]] = parsed[1]
            except OSError as e:
                print(f"  [!] Could not read {htaccess_path}: {e}")

        for filename in files:
            if filename in DEFAULT_FILE_EXCLUDES:
                continue
            rel_path = os.path.join(rel_root, filename) if rel_root else filename
            if matches(filename, rel_path, any_pats):
                continue

            src_filepath = os.path.join(root, filename)
            if os.path.islink(src_filepath):
                continue

            target_filepath = os.path.join(target_root, rel_path)

            # Resume fast path: if this exact symlink_path is already
            # recorded and both the vault file and the symlink exist, skip.
            cursor.execute("SELECT hash_id FROM book_locations WHERE symlink_path = ?", (rel_path,))
            row = cursor.fetchone()
            if row:
                existing_hash = row[0]
                if (os.path.exists(os.path.join(vault_dir, existing_hash))
                        and os.path.islink(target_filepath)):
                    n_skipped += 1
                    continue

            # --- Metadata: Hierarchy of Truth ---
            final_meta = {
                'title': None, 'author': None, 'publisher': None, 'tags': None,
                'series': None, 'languages': None, 'published': None,
                'identifiers': None, 'annotation': None, 'needs_review': False,
            }
            if filename in htaccess_data and htaccess_data[filename].get('title'):
                final_meta.update(htaccess_data[filename])
            else:
                lower = filename.lower()
                if lower.endswith('.pdf'):
                    extracted = extract_pdfinfo(src_filepath)
                    if extracted.get('title'):
                        final_meta.update(extracted)
                elif lower.endswith('.djvu'):
                    extracted = extract_djvu_meta(src_filepath)
                    if extracted.get('title'):
                        final_meta.update(extracted)
                else:
                    extracted = extract_ebook_meta(src_filepath)
                    if extracted.get('title'):
                        final_meta.update(extracted)

                if filename in htaccess_data:
                    for k, v in htaccess_data[filename].items():
                        if v is not None:
                            final_meta[k] = v

                if not final_meta['title']:
                    print(f"  [!] Warning: Metadata extraction failed for {rel_path}, falling back to filename.")
                    final_meta['title'] = os.path.splitext(filename)[0].replace('-', ' ').replace('_', ' ')
                    final_meta['needs_review'] = True

            try:
                src_mtime = os.stat(src_filepath).st_mtime
            except OSError as e:
                print(f"  [!] Failed to stat source {rel_path}: {e}")
                n_errors += 1
                continue

            # --- Ingest content into the vault ---
            try:
                file_hash = copy_and_hash(src_filepath, vault_dir)
                vault_filepath = os.path.join(vault_dir, file_hash)
            except (OSError, IOError) as e:
                print(f"  [!] Failed to ingest {rel_path} into vault: {e}")
                n_errors += 1
                continue

            # --- Cover & Symlink in target ---
            try:
                # Cover
                legacy_cover = os.path.join(root, ".covers", f"{filename}.jpg")
                vault_cover = os.path.join(covers_vault, f"{file_hash}.jpg")
                if not os.path.exists(vault_cover):
                    if os.path.exists(legacy_cover):
                        try:
                            shutil.copy2(legacy_cover, vault_cover)
                        except OSError as e:
                            print(f"  [!] Cover copy failed for {rel_path}: {e}")
                    elif filename.lower().endswith(('.jpg', '.jpeg')):
                        try:
                            from PIL import Image
                            resample_filter = getattr(Image, 'Resampling', Image).LANCZOS
                            with Image.open(src_filepath) as im:
                                im = im.convert("RGB")
                                w, h = im.size
                                if w > 300:
                                    new_h = max(1, int(h * 300 / w))
                                    im = im.resize((300, new_h), resample_filter)
                                    im.save(vault_cover, format="JPEG", quality=85)
                                else:
                                    shutil.copy2(src_filepath, vault_cover)
                        except Exception as e:
                            print(f"  [!] Failed to generate cover from image file {rel_path}: {e}")

                # Symlink
                target_parent = os.path.dirname(target_filepath)
                if target_parent:
                    os.makedirs(target_parent, exist_ok=True)
                if os.path.lexists(target_filepath):
                    if os.path.islink(target_filepath):
                        os.remove(target_filepath)
                    else:
                        print(f"  [!] Target path exists and is not a symlink, skipping symlink: {target_filepath}")
                        n_errors += 1
                        continue
                rel_vault_target = os.path.relpath(vault_filepath, target_parent)
                os.symlink(rel_vault_target, target_filepath)
            except Exception as e:
                print(f"  [!] Filesystem operation failed for {rel_path}: {e}")
                n_errors += 1
                continue

            # --- DB ---
            try:
                try:
                    import_date = datetime.fromtimestamp(src_mtime, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                except (ValueError, OverflowError, OSError) as e:
                    import_date = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                    print(f"  [!] Warning: Invalid modification time for {rel_path} ({e}). Using current time instead.")

                cursor.execute("""
                    INSERT OR IGNORE INTO books
                    (id, title, author, publisher, tags, series, languages,
                     published, identifiers, description, original_filename, needs_review, clearance,
                     import_date)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    file_hash, final_meta['title'], final_meta['author'], final_meta['publisher'],
                    final_meta['tags'], final_meta['series'], final_meta['languages'],
                    final_meta['published'], final_meta['identifiers'], final_meta['annotation'],
                    filename, final_meta['needs_review'], DEFAULT_BOOK_CLEARANCE,
                    import_date,
                ))
                cursor.execute("""
                    INSERT OR IGNORE INTO book_locations (hash_id, symlink_path)
                    VALUES (?, ?)
                """, (file_hash, rel_path))
            except sqlite3.Error as e:
                print(f"  [!] CRITICAL DB error during {rel_path}: {e}")
                conn.rollback()
                sys.exit(1)

            n_processed += 1

    try:
        conn.commit()
    except sqlite3.Error as e:
        print(f"  [!] CRITICAL DB commit error: {e}")
        conn.rollback()
        sys.exit(1)
    finally:
        conn.close()
    print("\nMigration complete!")
    print(f"  processed:       {n_processed}")
    print(f"  resumed/skipped: {n_skipped}")
    print(f"  errors:          {n_errors}")


if __name__ == "__main__":
    main()
