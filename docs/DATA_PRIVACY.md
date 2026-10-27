# Private research data and public source code

This repository is for general-purpose application source only. Do not commit real research media, participant information, transcripts, OCR observations, annotations, exports, local databases, credentials, or personal configuration.

## Storage boundary

The backend's default private directory is a **sibling of this repository**, named `<repository-name>_data`. `SIMULST_DATA_DIR` overrides it. An absolute value is used as given; a relative value is resolved from the repository's parent. Configuration pointing inside the repository is rejected at startup.

On Windows PowerShell, for example:

```powershell
$env:SIMULST_DATA_DIR = 'private-simulst-data'
cd backend
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The folder must be writable by the local user. The application creates one authoritative `simulst.sqlite3` for all projects. New project folders use `projects/<sanitized-name>_<short-id>/`; their actual relative paths are recorded in SQLite, so renaming a project does not move files. New media is stored under each project's `media/<media-id>/original.mp4`, with extracted audio under `derived/audio/<media-id>.wav`. The per-project `exports/` directory is reserved for privately retained downloads. Media imported before project support stays at its existing private `media/<media-id>/` path and is assigned to a Legacy project without moving the files. The application does not create per-project SQLite or JSON files. It never mounts the private directory as a public static directory. Media is served through explicit ID-based API endpoints that retrieve a recorded path and verify it remains under the configured private directory.

Archiving or removing a project or media item changes only its SQLite status. The records, relationships, annotations, MP4s and WAVs remain in place and can be restored through the manager. Storage Overview and audit are read-only. A file with no media-path reference is only **potentially** orphaned; backups and standalone demo files are classified separately. No automatic cleanup is performed.

Project exports download to the browser's configured download folder; they are **not** automatically copied into the private project folder. They contain annotation text and possibly media filenames, so treat downloaded JSON, CSV and ZIP files as research data. Exports exclude original video bytes and absolute private file paths. `Open Project Folder` opens the selected private project folder on Windows.

Before changing the SQLite schema, stop the API, create an independent SQLite backup using the SQLite backup API, run `PRAGMA integrity_check` on the copy, and compare row counts for every table. The project migration is idempotent and preserves old media IDs, annotation IDs, intervals, text and paths. Older annotations did not record creation/update timestamps; the migration records its execution time and marks that fact in their metadata.

The old repository-local `data/` path is ignored for safety and is no longer used by the application. If it contains legacy material, copy it into the sibling private directory and verify the copy before moving any original. Do not run a blanket repository cleanup command against research data. Keep independent backups according to your research protocol.

## Before publishing

1. Run `python scripts/check_public_repo.py` from the repository root. It checks staged, tracked, and visible untracked files plus path names in Git history.
2. Review `git status --short` and `git diff --cached --name-only`. Stage only intended source files one by one; never use `git add .` for a public release.
3. If the checker reports a file, remove it from the staging area or public source directory **without deleting the private original**. Check whether a historical commit contains it; if so, do not push until history has been reviewed and cleaned with an explicit plan.
4. Run the checker again immediately before any public push. The optional pre-commit hook is a local reminder, not a replacement for this review.

To install the optional hook in a local Git checkout, copy `scripts/hooks/pre-commit` to `.git/hooks/pre-commit` and make it executable on systems that require that. It runs the checker and blocks a commit when the checker fails.

## What the checker can and cannot detect

The checker conservatively flags common media/database/export extensions, private directories, likely credentials, email addresses, and common user-home absolute paths. It also checks Git history **path names**. False positives are possible in documentation or code examples; investigate them rather than bypassing the hook. It cannot prove that a transcript with no obvious identifiers is anonymous, infer consent, detect every secret format, or scrub earlier Git history automatically. Human review of every staged file remains necessary.

Only explicitly approved, fictional synthetic demonstration material should ever be added to public source. The repository currently does not include demonstration media or participant-like sample records.
