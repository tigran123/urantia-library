#!/usr/bin/env python3

import os
import subprocess
import sys

BOOKS_DIR = "/Books"
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp')

def main():
    print(f"Scanning {BOOKS_DIR} for images to generate thumbnails...")
    count = 0
    for root, dirs, files in os.walk(BOOKS_DIR):
        # Exclude special directories
        for excluded in ['.covers', 'webapp', 'urantia-library']:
            if excluded in dirs:
                dirs.remove(excluded)
                
        for file in files:
            if file.lower().endswith(IMAGE_EXTS):
                file_path = os.path.join(root, file)
                cover_dir = os.path.join(root, ".covers")
                cover_path = os.path.join(cover_dir, f"{file}.jpg")
                
                # If a cover already exists
                if os.path.exists(cover_path) or os.path.islink(cover_path):
                    if os.path.islink(cover_path):
                        # The old register-books.py would symlink default-cover.jpg for images.
                        # We want to replace these symlinks with actual thumbnails.
                        target = os.readlink(cover_path)
                        if "default-cover.jpg" in target:
                            os.unlink(cover_path)
                        else:
                            continue
                    else:
                        continue
                        
                # Create .covers directory if it doesn't exist
                if not os.path.exists(cover_dir):
                    os.makedirs(cover_dir, exist_ok=True)
                    
                print(f"Generating thumbnail for: {file_path}")
                try:
                    # Use ImageMagick to resize the image to 300px wide (maintaining aspect ratio)
                    subprocess.run(["convert", "-geometry", "300", file_path, cover_path], check=True)
                    count += 1
                except Exception as e:
                    print(f"Failed to generate thumbnail for {file_path}: {e}", file=sys.stderr)

    print(f"Done! Generated {count} thumbnails.")

if __name__ == "__main__":
    main()
