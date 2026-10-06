# Photo Library Manager

A native Windows app for face- and object-recognition-powered photo library
management: auto-detects faces and common objects/scenes, groups faces into
people you name once, lets you filter your library by person or object,
proposes a folder/rename structure it only applies after you approve it, and
can auto-index your external photo drive the moment you plug it in.

## Milestone 3: Tray app + auto-index on drive connect

**This is the way to actually use the app day to day:**
```
python run_tray.py
```
This puts a tray icon in your Windows system tray and keeps running in the
background. Plug in your external photo drive and it's detected by its
volume serial number (stable across drive-letter changes) and indexing
starts automatically — no clicking anything. A tray notification tells you
when a drive was detected and whether indexing started.

- **Left-click the tray icon** (or "Open Photo Library Manager") — opens the
  full Library/People/Organize window. Closing that window just hides it;
  the tray watcher keeps running.
- **"Auto-index when a drive connects"** — checkable in the tray menu; turn
  it off if you'd rather trigger indexing manually via the app's toolbar.
- **Quit** — actually exits, stopping the drive watcher too.

**Run it automatically at Windows startup:** press `Win+R`, type
`shell:startup`, and drop a shortcut to `run_tray.py` (or the packaged
`.exe`, once you build that — see below) in the folder that opens.

This was smoke-tested by simulating a drive-connect event end-to-end
(bypassing real USB hardware, which isn't available in a dev sandbox): the
tray app correctly received the event, started background indexing, and
the library reflected the new photos — confirmed before being handed to
you. `core/drive_watcher.py` (the actual Windows drive-detection code, using
win32api/WMI) can only run on Windows itself, so test that specific piece
by literally plugging in a drive once you're on your machine.

## Milestone 2: Desktop GUI (manual mode)

If you'd rather not run the tray/background version, you can run just the
window and index folders manually via the toolbar:
```
python run_gui.py
```

- **Toolbar → "Index a folder / drive..."** — pick a folder (or your whole
  external drive once you've tested on a subset). Indexing runs in the
  background with a progress bar; the window stays responsive.
- **Library tab** — thumbnail grid of everything indexed. Filter by filename
  or by recognized person. Double-click a photo for a detail view.
- **People tab** — every detected face cluster shows as a card with a
  representative thumbnail. Type a name once (e.g. "Mom") and it's applied
  to every photo with that face. Cards also let you merge two clusters that
  turned out to be the same person.
- **Organize tab** — pick a sort scheme (by date / by person / both),
  optionally enable renaming, click Preview to see exactly what would move
  where, check/uncheck individual rows, then Apply. Nothing on disk changes
  until you click Apply.
- **"Re-cluster faces"** toolbar button — re-runs clustering on demand (also
  runs automatically after each indexing pass).

This was smoke-tested in a headless environment end-to-end: indexing via the
background worker, face detection + clustering, naming a person, filtering
the library by that person, and previewing a sort — all confirmed working
before being handed to you.

## Milestone 4: Object recognition + packaging

- **Object detection**: every indexed photo is also run through a YOLOv8n
  object detector (`core/object_engine.py`), tagging common objects/scenes
  (person, dog, car, food, ...). The Library tab has an "Object" filter next
  to the existing person filter, and the Organize tab has a "By object" sort
  scheme. One-time setup before this works (not needed for face recognition):
  ```
  pip install ultralytics
  yolo export model=yolov8n.pt format=onnx imgsz=640
  ```
  then move the resulting `yolov8n.onnx` into `data/models/yolov8n.onnx`. If
  it's missing, indexing still works fine — object detection is just skipped
  with a console message, exactly like turning off face detection.

- **Packaging**: build a standalone `.exe` with PyInstaller —
  ```
  .\build.ps1
  ```
  produces `dist\PhotoLibraryManager\PhotoLibraryManager.exe`. Copy the whole
  `dist\PhotoLibraryManager\` folder wherever you want (it's a onedir build,
  not a single file), then drop a shortcut to the `.exe` into `shell:startup`
  for it to run automatically at login — no Python install needed anymore.

## Milestone 5: Dashboard, duplicates, IMG_#### renaming, installer

- **Dashboard tab** (now the first tab): library stats at a glance (photos,
  faces, people, objects, duplicate groups) and a list of every drive/folder
  you've ever indexed, each with a "Use this drive" button. The toolbar also
  has an "Active drive" dropdown — whichever drive you pick there (or last had
  active) is what the Organize and Duplicates tabs operate on, and it's
  remembered across restarts, so you don't need to re-browse to a folder every
  time you reopen the app.
- **Duplicates tab**: reviews the exact-duplicate copies the indexer already
  tracks whenever the same photo turns up on more than one drive/folder. Each
  group shows every copy as a "keep this one" choice; applying deletes every
  other copy from disk and updates the database. Byte-identical duplicates
  only (not visually-similar-but-different photos).
- **IMG_#### renaming**: in the Organize tab, click "IMG_#### preset" to
  rename files sequentially in date-taken order (`IMG_0001.jpg`, `IMG_0002.jpg`,
  ...) instead of whatever random name they came with. Like all renaming
  here, nothing happens until you click Apply, and re-previewing after
  applying shows no further changes (it's idempotent).
- **Thumbnail size slider** in the Library tab.
- **Installer**: build a proper Windows installer (Start Menu entry — shows
  up in Windows search — Desktop shortcut, uninstaller) instead of just a raw
  `.exe` folder:
  ```
  .\build_installer.ps1
  ```
  produces `Output\PhotoLibraryManagerSetup.exe`. One-time setup on the build
  machine (not needed by whoever runs the installer):
  ```
  winget install JRSoftware.InnoSetup
  ```

## Milestone 6: Update manager (current)

- **Auto-update checks**: the tray app checks GitHub Releases for
  `AbhiMokkapati/photo-library-manager` a few seconds after launch (silently — a
  failed/offline check never bugs you). If a newer version is published,
  a dialog offers to download and run the new installer, then quits the
  running app so the installer can overwrite its files. Toggle this from
  the tray menu ("Check for updates on startup"), or trigger it manually
  with "Check for updates now".
- **Cutting a release**: bump `core/version.py`'s `__version__`, run
  `.\build_installer.ps1` (it now reads that version and passes it into
  Inno Setup, so `installer.iss` never drifts out of sync), then create a
  GitHub Release on the repo tagged `vX.Y.Z` and attach the resulting
  `Output\PhotoLibraryManagerSetup.exe` as a release asset — the update
  checker looks for an asset whose name ends in `Setup.exe`.

---

## Milestone 1: Core Engine

The indexing/recognition engine underneath the GUI. You can also run it
directly from the command line if you want to script something or test
against a folder without the GUI.

## What's in this milestone

- `core/db.py` — SQLite schema: photos, faces, people, albums, drives
- `core/exif_utils.py` — extracts date/camera/GPS from photos
- `core/face_engine.py` — face detection + recognition embeddings (InsightFace)
- `core/indexer.py` — walks a folder, hashes/dedupes, indexes everything
- `core/clustering.py` — auto-groups detected faces into "people"
- `core/auto_sort.py` — proposes folder structure / renaming, never moves files until you approve
- `core/drive_watcher.py` — Windows-only: detects when an external drive is plugged in and auto-indexes it
- `run_index.py` — command-line tool to run all of the above

## Setup (on your Windows machine)

1. Install Python 3.11 or 3.12 from python.org (check "Add to PATH" during install)
2. Open a terminal in this folder and run:
   ```
   pip install -r requirements.txt
   ```
   First run will download ~300MB of face recognition model weights automatically.

## Try it now

Index a folder (start small — a subfolder with a few hundred photos — before pointing it at your whole drive):

```
python run_index.py "E:\Photos\2024" --cluster --propose-sort by_date_and_person
```

- `--cluster` groups detected faces into people (unnamed until you label them — that's a GUI feature next)
- `--propose-sort` prints what a reorganization *would* look like, without touching any files
- Drop `--cluster` for a faster first pass if you just want to see indexing speed on your library
- Everything gets written to `data/library.db` (SQLite — you can open it with any DB browser to poke around)

## What's next

1. **GUI** (PySide6) — browse your library, see face clusters as tap-to-name cards, approve/edit sort proposals visually, review thumbnails in a grid
2. **Drive auto-launch** — wire `drive_watcher.py` into a small always-on tray app so plugging in your external drive triggers indexing automatically, with a notification instead of you running commands
3. **Packaging** — PyInstaller build so the whole thing is a double-click `.exe`, no Python install needed for daily use

## Notes on accuracy tuning

- `core/face_engine.py`: `_MIN_DET_SCORE` (currently 0.55) — raise if you're getting false-positive face detections, lower if faces are being missed
- `core/clustering.py`: `CORE_COSINE` (0.60, DBSCAN neighbour similarity), `DEFAULT_MIN_SAMPLES` (3), `MATCH_THRESHOLD` (0.50) / `MATCH_MARGIN` (0.05) for attaching leftovers, `AUTO_CONFIRM_THRESHOLD` (0.65, below this an assignment goes to the review queue), and `MIN_QUALITY_DET_SCORE` / `MIN_QUALITY_FACE_PX` (faces below these never form clusters, only attach to existing ones). Raise `CORE_COSINE` for fewer false merges, lower it for less splitting. `suggest_person_merges()` lists people that are probably the same person.
