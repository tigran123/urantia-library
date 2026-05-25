# Urantia Library

Welcome to the _Urantia Library_ — a curated, in-browser reading library for a
personal collection of books, manuscripts, dictionaries, encyclopedias, and
other reference material. Everything is read directly in your browser; nothing
needs to be installed.

The library is organised by topic. Use the navigation in the top bar to browse
the directory tree, search by title or author, or jump to your bookshelf of
favourites and books in progress.

**No account needed to read.** The catalog is open to guests — anyone can
browse the directory tree, open books, and search without signing in. Signing
in is what enables the per-reader features below: bookshelf, reading progress,
highlights, ratings, comments, and feedback threads.

## Reading

- **Many formats, one reader.** PDF, EPUB, DJVU, FB2, Markdown, HTML, and image
  files all open inline in the browser — no separate apps, no downloads.
- **Tables of contents.** PDF, EPUB, DJVU, FB2, and Markdown documents expose
  their built-in TOC in a sidebar so you can jump between chapters quickly.
- **Resume where you left off.** The reader remembers your position in every
  book (page, chapter, or scroll location, depending on format) and restores
  it automatically the next time you open the book.
- **Book covers.** Books display their cover thumbnail in directory listings,
  search results, and on your bookshelf.
- **Direct links.** Every page in the library is bookmarkable — share a link
  to a directory, a book, or a search result and it will open at the same
  place for the recipient.

## Search

- **Full-text metadata search.** Search across title, author, publisher,
  description, tags, series, and identifiers in a single query.
- **Unicode-aware.** Case-insensitive search works correctly for Cyrillic and
  other non-Latin scripts.
- **Quick filters.** Narrow a search with inline operators in the query:
  `path:Topic` scopes the search to a directory subtree, `ext:pdf` scopes it
  to a file format.
- **Sort and lay out.** Sort results by relevance, file size, or directory;
  switch between list and grid views, and adjust the thumbnail size of the
  grid.
- **Configurable page size.** Choose how many results you want per page from
  your account settings.

## Your bookshelf

- **Favourite books.** Click on the 'bookmark' icon on any book to add it to your
  bookshelf for one-click access.
- **Favourite directories.** Bookmark entire topic directories the same way —
  useful for collections you return to often.
- **Reading-progress strip.** Books with saved progress appear on your
  bookshelf with a progress bar so you can pick up the next session at a
  glance.

## Annotations, ratings, and comments

- **Highlights and notes.** Select text in any supported format (PDF, EPUB,
  HTML, Markdown, FB2) to highlight it or attach a private note. Your
  highlights and notes are stored on the server, follow you between devices,
  and survive between sessions.
- **Private by default, optionally public.** Annotations start private. You
  can choose to share an annotation publicly so other readers see it — public
  annotations go through admin review before they appear to others.
- **Star ratings.** Rate any book from 1 to 5 stars; the book's average
  rating and total count are visible to every reader.
- **Threaded comments.** Leave one top-level comment per book, and reply to
  any other reader's comment. Top-level comments are reviewed by an admin
  before they become visible to everyone. Replies appear under the comment
  they answer.

## Account

- **Registration with admin approval.** Anyone can request an account. An
  admin reviews the request and, when approved, you receive an email with a
  one-time link to set your password.
- **Profile.** Upload an avatar, set a display name (used wherever your name
  appears instead of the email local-part), and adjust search results per
  page.
- **Sessions.** Sign in from as many browsers as you like. Each browser shows
  up as its own session, and signing out only ends the current one.
- **Email notifications.** Choose what you want to be emailed about: replies
  to your feedback threads, status changes on issues you opened, and an
  optional weekly summary. Toggles live in your account settings.
- **Light and dark themes.** Switch between light and dark mode at any time.
  The choice is remembered per browser.
- **Multilingual interface.** The UI is available in multiple languages — use
  the language switcher in the top bar.

## Talking to the librarians

The _Urantia Library_ has a built-in **Contact admin** system for anything that
doesn't belong in a public book comment — bug reports, feature suggestions,
metadata fixes, requests to acquire a particular book, copyright concerns,
duplicate-file reports, and general questions.

- **Categorised threads.** Each message is tagged with a category (general,
  bug, feature, book-related, acquire, other) so the right person sees it
  first.
- **Per-book context.** When you open a thread from inside a book viewer, the
  current book — and the page you were on for PDF / DJVU — is automatically
  attached so admins know exactly what you mean.
- **Screenshot attachments.** Drop a screenshot into the form to illustrate
  what you're seeing.
- **Choose your audience.** Send to all admins, or pick specific ones from a
  list. You can include yourself as a recipient as a first-class
  "self-reminder".
- **Threaded conversation.** Replies appear in the thread the way an email
  conversation does, with status changes (new → triage → in progress →
  waiting → resolved → closed) recorded inline so the history is auditable.
- **My feedback.** Every thread you have open or have ever opened is listed
  on your **My feedback** page with its current status and unread-reply
  indicator.
- **Resolve threads yourself.** Once your question is answered, you can mark
  the thread resolved without waiting for an admin to do it.

## What admins can do from the UI

Everything an administrator needs to run the library lives inside the same
web interface — there is no separate admin tool.

- **Books.**
  - Upload new books in any supported format. The upload flow extracts
    metadata, generates a cover, lets the admin preview and edit every field,
    and stages the file for review before committing.
  - Edit existing book metadata (title, author, publisher, description, tags,
    series, languages, identifiers) and replace or re-extract the cover image
    from the book itself.
  - Move books and entire directories between topic folders. Renames and
    moves preserve every reader's progress, favourites, ratings, comments,
    and annotations — those follow the *content*, not the path.
  - Delete books when appropriate.
- **Integrity.**
  - Verify a single book's storage integrity on demand (quick check or full
    hash recompute).
  - Launch a library-wide integrity job with live progress and failure
    summaries; cancel it from the same page if needed.
- **Users.**
  - Approve or reject new registration requests.
  - Manage existing accounts — activate, deactivate, adjust profile fields.
  - View and terminate any active session for any user.
- **Moderation.**
  - Approve or remove pending comments. Recent comments are also listed so
    you can revisit moderation decisions.
  - Approve or remove pending public annotations.
- **Feedback inbox.**
  - See every feedback thread, filtered by status (new / open / triage / in
    progress / waiting / resolved / closed / archived) or scoped to threads
    assigned to you.
  - Reply publicly (visible to the user who opened the thread) or leave an
    internal note (admin-only).
  - Reassign a thread to another admin, change its status, archive it, or
    delete it outright.
  - Tune the digest scheduler: digest interval, minimum batch size before a
    digest is sent, urgent-bypass behaviour, and extra non-admin email
    addresses to copy. Force-send a digest immediately when needed.

## Privacy and persistence

Your account, your reading progress, your favourites, your highlights, your
notes, and your feedback threads all live on the server tied to your account —
not to a specific browser. Sign in from a different machine and everything is
exactly where you left it.

Enjoy the library.
