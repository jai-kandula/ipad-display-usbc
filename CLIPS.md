# Clips on the iPad

Open **Clips** from the iPad menu, or from the **Clips** button above the
button deck. You get one list of every replay clip on the PC, newest
first. Filter it by app or search it by title, game or tag. Tap a clip to
play it on the iPad, or use **Play on PC** or **Show in folder**.

## Found by themselves

If these folders exist, their clips show up with nothing to set up:

| App | Folder |
| --- | --- |
| NVIDIA (NVIDIA app / ShadowPlay) | `Videos\NVIDIA`, `Videos\Captures` |
| Medal | `Videos\Medal`, `C:\Medal\Clips` |
| Insights Capture | `Videos\Insights Capture` |
| Outplayed | `Videos\Overwolf\Outplayed` |

If an app saves somewhere else (check its settings), add that folder as
shown below.

## Adding any app: a folder

Add the app to `settings.json` next to start.bat (create the file if it
isn't there), then restart the host:

```json
{
  "clip_sources": [
    {"name": "Ascent", "folder": "D:\\Clips\\Ascent"}
  ]
}
```

Every video in that folder and its subfolders (.mp4, .mkv, .mov, .webm,
.m4v) shows up. To give a clip a title, game or tags, put a JSON file next
to it with the same name, for example `ace.mp4` and `ace.json`:

```json
{"title": "Ace on Bank", "game": "Rainbow Six Siege", "tags": ["ace"], "thumb": "ace.jpg"}
```

## For your own clipping tool: a manifest

Have your tool keep one JSON file that lists its clips, and point a source
at it:

```json
{"clip_sources": [{"name": "My clipper", "manifest": "C:\\MyClipper\\clips.json"}]}
```

`clips.json`:

```json
{
  "clips": [
    {
      "file": "2026-11-23 ace.mp4",
      "title": "Ace on Bank",
      "game": "Rainbow Six Siege",
      "created": "2026-11-23T20:14:05Z",
      "duration": 30.5,
      "thumb": "2026-11-23 ace.jpg",
      "tags": ["ace", "ranked"]
    }
  ]
}
```

- Only `file` is required.
- Paths can be absolute, or relative to the manifest.
- `created` is ISO 8601 and sets the order; without it, the file's date is used.
- `thumb` is a JPEG or PNG. Without it, the PC makes a picture from one second into the clip, once.
- The list refreshes within 10 seconds when the file changes. Write it atomically: write `clips.json.tmp`, then rename it.

The iPad can only open files that one of these sources lists. It can never
ask for any other path.
