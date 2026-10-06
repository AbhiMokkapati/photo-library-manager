## Photo Library Manager 1.1.0

### Better face grouping
- Faces are now grouped far more completely. On a real 460-face library, unassigned faces dropped from 753 (of 785 stored rows) to 67.
- Duplicate face records (the same face stored several times after a retried scan) are cleaned up automatically on first launch and no longer created.
- People you have confirmed always take priority: matching faces join them before any new group is formed.
- A single near-duplicate pair no longer creates a "person", and blurry or tiny faces can no longer link two different people together.
- Automatic groupings are no longer locked in as confirmed, so re-clustering can keep improving them. Only your own confirmations are permanent.
- "Needs confirmation" now lists only matches the app is genuinely unsure about.

### Merge suggestions
- New **Merge suggestions** button on the People tab lists people who look like the same person split into two groups. Merge them, or mark them as different people and they will not be suggested again.

### Fixes
- Photos edited in place (same path, new content) are now re-indexed instead of failing on every scan.
- Re-running a scan no longer duplicates detected objects.
- Faces the app is unsure about no longer decide where files are moved or what they are renamed to in the Organize tab.
- The Recycle Bin (`$RECYCLE.BIN`) and folders named like images are no longer indexed.
- Library grid no longer shows duplicate thumbnails when filtering quickly on large libraries.
- Plugging in a drive during a scan now queues it instead of silently ignoring it. Two clustering passes can no longer run at once.
- An invalid rename pattern now shows an explanation instead of doing nothing.
- Faster indexing and clustering on large libraries.

Existing libraries are migrated automatically on first launch.
