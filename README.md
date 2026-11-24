# iPad Display: use your iPad as a second monitor for Windows

This turns your iPad into an extra screen for your Windows laptop over Wi-Fi, using a web app in Safari. You can touch the iPad to click, drag, scroll and type, and the Apple Pencil works as a precise mouse.

Built for **Python 3.15** (works on 3.10 and newer).

> **USB-C cable (recommended):** plug the iPad into the laptop and use the native iPad Display app. See **[USB-C.md](USB-C.md)**.
>
> **Ethernet cable instead?** For the lowest delay and full quality, see **[WIRED.md](WIRED.md)**. It uses a USB-C Ethernet adapter and a cable, with no Wi-Fi involved.

## How it works

Windows can only extend your desktop onto a screen it thinks is plugged in, and a website can't make Windows think that. So there are three pieces:

1. **Virtual Display Driver** is free and open source. It adds a fake monitor to Windows.
2. **The host** (`start.bat` runs `server.py`) captures that monitor and streams it over your Wi-Fi.
3. **The iPad web app** in Safari shows the stream and sends your touches back.

## One-time setup (about 10 minutes)

### 1. Install Python 3.15
Download **Python 3.15** from https://www.python.org/downloads/ (the "Windows installer (64-bit)"). On the first screen, **tick "Add python.exe to PATH"**, then click Install Now.

> If you ran an older version of this app before: delete the `.venv` folder inside `ipad-display` so it's rebuilt on Python 3.15.

### 2. Install the Virtual Display Driver (the GitHub download to use)
- **Project:** https://github.com/VirtualDrivers/Virtual-Display-Driver
- **Release to download:** https://github.com/VirtualDrivers/Virtual-Display-Driver/releases/tag/25.7.23, the release marked "Latest" when this was written. If a newer one is marked Latest, use that.
- **File:** under *Assets*, download **`VirtualDriverControl-25.7.23.zip`** (the Virtual Driver Control app, which is signed). The "Driver.Only" zip is for manual installs, so skip it.

Then:
1. Extract the zip to a folder, for example `C:\VDD`.
2. Run **`VDC.exe`** and click **Install**. Accept the Windows prompt. You need to be an administrator.
3. If it says `vcruntime140.dll` is missing, install the Microsoft Visual C++ Redistributable (x64) and try again.

Alternative: open Terminal as administrator and run `winget install --id=VirtualDrivers.Virtual-Display-Driver -e`

### 3. Set the virtual screen to your iPad's resolution
Open **Settings > System > Display** and select the new display. Choose **Extend these displays**. Drag it in the diagram to the side where your iPad will sit. Then set **Display resolution** and **Scale** from the table below.

**Sharpest** matches the iPad's real pixels exactly (1:1, no blur). **Fastest** is half of that. It means 4x fewer pixels to send, so delay is lowest, but it's slightly softer.

| iPad model | Sharpest (scale 200%) | Fastest (scale 100%) |
|---|---|---|
| iPad Pro 13-inch (M4 and later) | 2752 × 2064 | 1376 × 1032 |
| iPad Pro 12.9-inch (3rd to 6th gen) | 2732 × 2048 | 1366 × 1024 |
| iPad Air 13-inch (M2 and later) | 2732 × 2048 | 1366 × 1024 |
| iPad Pro 11-inch (M4 and later) | 2420 × 1668 | 1210 × 834 |
| iPad Pro 11-inch (1st to 4th gen) | 2388 × 1668 | 1194 × 834 |
| iPad Air 11-inch (M2 and later) | 2360 × 1640 | 1180 × 820 |
| iPad Air (4th or 5th gen) | 2360 × 1640 | 1180 × 820 |
| iPad (A16) / iPad (10th gen) | 2360 × 1640 | 1180 × 820 |
| iPad Air (3rd gen) | 2224 × 1668 | 1112 × 834 |
| iPad (8th or 9th gen) | 2160 × 1620 | 1080 × 810 |
| iPad mini (A17 Pro) / iPad mini (6th gen) | 2266 × 1488 | 1133 × 744 |
| iPad mini (5th gen) | 2048 × 1536 | 1024 × 768 |

Not sure which iPad you have? The web app detects it and shows your two resolutions on the connect screen. It also tells you whether the PC screen matches.

**If your resolution isn't in Windows' list:** the driver only offers resolutions listed in its settings file. Open **`C:\VirtualDisplayDriver\vdd_settings.xml`** in Notepad (run Notepad as administrator), back the file up first, and inside the existing `<resolutions>` section add an entry for your iPad, for example:

```xml
<resolution>
    <width>2420</width>
    <height>1668</height>
    <refresh_rate>60</refresh_rate>
</resolution>
```

Add one for the Fastest size too if you want it. Save, then apply the change: in Device Manager > Display adapters, right-click **Virtual Display Driver** and choose Disable, then Enable. Rebooting also works, and so does the reload button in VDC.

### 4. Copy this folder to the laptop
Put the whole `ipad-display` folder somewhere like `Documents\ipad-display`.

## Every time you want to use it

1. On the laptop, double-click **`start.bat`**. The first run takes a minute to install what it needs.
   - If Windows Firewall asks, tick **Private networks** and click **Allow**.
   - The window shows an address like `http://192.168.1.23:8765` and a **6-digit PIN**. Leave the window open.
2. On the iPad, which must be on the same Wi-Fi, open **Safari** and type that address.
3. Enter the PIN. Check **Your iPad** shows the right model, and pick the virtual screen under **Screen to show**. Then tap **Connect**.
4. **Full screen (do this once):** tap Share, then **Add to Home Screen**. From now on open "iPad Display" from your home screen. It's full screen and reconnects by itself.

## Turn the iPad screen on and off automatically (optional, once)

Normally the virtual screen stays plugged in to Windows even when the iPad isn't, so windows and your mouse can get lost on it. To have it appear only while you use the iPad:

1. Double-click **`display-setup.bat`** in this folder and click **Yes** on the Windows admin prompt. It turns the screen off and on once as a test.
2. From now on, **`start.bat` turns the virtual screen on**, and **closing the start.bat window turns it off** (the X button or Ctrl+C both work). Windows remembers its resolution and position, and moves any windows on it back to your laptop screen.

It also switches off when the laptop restarts. If you move this folder, run `display-setup.bat` again. To undo it, run `display-setup.bat remove`. To leave the screen alone for one session, run `start.bat --keep-screen`. If it doesn't switch, look in `vdd.log` in this folder.

## Portrait, screen size and text size

In **☰ > Screen** you can change the iPad's screen in Windows without opening Windows settings:

- **Sharp**: the iPad's full resolution at 200% text size. Pixel-perfect, with text the size you'd expect on an iPad.
- **Roomy**: full resolution at 150%. Still sharp, with smaller text and more room.
- **Max space**: full resolution at 100%. Tiny text, the most room.
- **Fast**: half the resolution at 100%. A quarter of the pixels to send, so it's the quickest, and a little softer. It's faded out if the Virtual Display Driver doesn't offer that resolution; add it in the driver's settings to use it.
- **Turn with the iPad**: hold the iPad upright and the screen turns portrait in Windows; turn it back and it goes landscape again.

Only the iPad's screen is changed. The laptop's main screen is always left alone.

## Icon next to the clock

While the host runs, an iPad icon sits next to the Windows clock (it may be under the **^** arrow). It's **blue** while the iPad is connected and grey while it waits. Right-click it to:
- see what the iPad is doing,
- switch **Profile** (Work, Gaming, or your own),
- open the log or this folder,
- **Quit iPad Display** (this also turns the iPad screen off, like closing the window).

start.bat installs the small `pystray` package for this, so run start.bat once after updating. To hide the icon, start with `start.bat --no-tray`.

## When the laptop sleeps

If the laptop goes to sleep, closes its lid or shuts down, the iPad goes black after 15 seconds and the app turns its brightness right down, so it doesn't sit there lit up. When the laptop wakes, the picture and brightness come back by themselves. Tap the iPad to brighten it in the meantime. The iPad app also keeps the iPad awake while it's connected, and shows its battery in the status corner (**☰ > Status corner**).

## Put apps on the iPad

**☰ > Apps on the iPad** lists the windows open on the laptop.
- **Move here** puts that window on the iPad, maximized.
- **📌 Keep** does that for the app every time: whenever the iPad is connected, its windows move to the iPad as they open (for example Discord, Spotify or your notes). You can still drag one back to the laptop. Tap 📌 Keep again to stop.

Kept apps are saved in `pins.json` in this folder.

## Profiles and the button deck (Work and Gaming)

A **profile** says what the iPad is for right now. Switch with **☰ > Profile**, or the buttons along the top of the deck. Two come ready to use:

- **💼 Work**: the iPad is your second screen. A panel of shortcut buttons (copy, paste, screenshot, task view and more) slides out when you tap the small **‹** tab on the right edge.
- **🎮 Gaming**: the whole iPad becomes a grid of big buttons, like a Stream Deck: mute mic, deafen, save a clip, screenshot, volume and music. **Nothing is streamed**, and if you ran `display-setup.bat` the iPad screen is also removed from Windows, so the game runs as if the iPad weren't there. A tap costs the laptop about as much as pressing a key.

To change things, tap **Edit** on the deck (or **☰ > Edit buttons and profiles**):

- Tap a button to change it, or **+ Add button** for a new one. A button can **press keys** (like `ctrl+shift+m`, `win+d`, `alt+f10`, `f13`, `playpause`, `volup`), **type text**, **open** an app, file or link (`obs64.exe`, `steam://`, `https://…`), or **switch profile**. Use the ◀ ▶ buttons to move it.
- **⚙︎ Profile** sets the name, icon, buttons per row, and whether the profile shows the PC screen, the button panel, and keeps the iPad screen in Windows. **+ Profile** makes a new one (for example one per game).
- Tip: some games ignore keys that come from another device. Buttons work best when they press shortcuts for apps like Discord, OBS or NVIDIA. Give the app a spare shortcut such as `ctrl+alt+f13` and put the same one on the button.

Your profiles are saved on the laptop in `profiles.json` in this folder, so they're kept when you update the app.

### OBS and Twitch chat

Buttons can drive OBS Studio directly (no hotkeys needed): go live or end the stream, record, pause the recording, save a replay, turn the replay buffer or virtual camera on and off, switch scene, and mute or unmute a source like your mic. Each button lights up while it's on (live, recording, on that scene, muted). The **OBS** live tile shows LIVE or REC with the time, the scene, and while live the dropped frames and bitrate.

1. In OBS: **Tools > WebSocket Server Settings**, tick **Enable WebSocket server**, then **Show Connect Info** and copy the password.
2. On the iPad: **Settings > Buttons > OBS Studio**, paste the password, tap **Save and test**.
3. **Edit** the deck, add a button, pick **OBS**, and choose what it does. Macros can have OBS steps too, like `scene:BRB` then `mute:Mic/Aux`.

The **Twitch chat** tile shows your chat live. Type your channel name in **Settings > Buttons > Twitch chat**. It reads chat straight from Twitch on the iPad (read-only, no login), so the laptop does nothing for it.

### School: focus timer, calendar, math pad, lab capture, PDF notes

Tap **School** on the deck (or **☰ > School**).

- **Focus**: a 25, 50 or 90 minute focus block, then a break. While you focus, the laptop minimises apps on your block list (Discord, Steam, Siege...) and browser tabs with words like YouTube or Twitch in their title the moment they come to the front. Edit both lists under **What gets blocked**. Nothing is watched outside a focus block. The **Focus timer** live tile shows the time left.
- **Calendar**: your classes, exams and due dates for the next 7 days. Paste your calendar's private iCal link once (Google Calendar on a computer: Settings, click your calendar, "Secret address in iCal format"). Weekly classes, moved and cancelled ones are handled. The **Next class** live tile shows what's next.
- **Math**: write a line with the Pencil (Scribble turns it into text) and tap **=**: `x^2 - 5x + 6 = 0`, `diff(x^2 sin(x), x)`, `integrate(x e^x, x)`, `factor(...)`, and units like `300 knots to m/s`, `14.7 psi to kPa`, `1.225 kg/m^3 to slug/ft^3`, `20 C to F`. Tap an answer to put it on the laptop's clipboard. Algebra uses sympy, which start.bat installs.
- **Lab**: name the experiment, set the columns with units, type readings (Enter adds the next row) and take photos of the setup. **Save to PC** writes a folder in `lab` with `data.csv` (opens in Excel), your notes and the photos. The sheet is kept on the iPad until you start a new one.
- **PDF notes**: lecture slides and worksheets from your Documents folder (or `notes_folder` in settings.json). Write on them with the Pencil (fingers scroll; **Finger** lets a finger write), with a highlighter, eraser and undo. **Save** writes `<name> (notes).pdf` next to the original, which is never changed. The PDF viewer loads from the internet the first time.

### Dev: build and test, Git, logs, Minecraft server, snippets

Tap **Dev** on the deck (or **☰ > Dev**). Everything here runs only when you open it or tap something.

- **Tasks**: tap **Edit projects**, add a project (a name and its folder, like `C:\Code\mymod`) and its commands, like `gradlew build`, `gradlew runClient` or `npm test`. Tap a task to see it, tap it again to run it. Its output scrolls by live (errors in red, warnings in yellow), with a dot that turns green or red when it ends and a **Stop** button. A deck button can run a task too (button type **Task**).
- **Git**: the branch, changed files, what there is to push or pull, the last commits, and **Fetch**, **Pull** (fast-forward only, so it never makes a merge) and **Push**. The **Git** live tile shows the first project.
- **Logs**: follows a project's log file (set it in the project, like `run\logs\latest.log`) or the Minecraft server's log, live.
- **Server**: your Minecraft server over RCON: who's online, TPS (Paper, Purpur, Spigot, Forge and NeoForge report it), Save world, Stop server (tap twice), and any console command. In the server's `server.properties` set `enable-rcon=true` and `rcon.password=...`, restart it, then fill in **Connection** on this screen (and the server folder, for its log). The **Minecraft server** live tile shows players and TPS; a deck button can send a command (button type **Server**).
- **Snippets**: code, commands or text you use often. **Type** types it into the window in front on the PC; **Copy** puts it on the PC's clipboard.

### Siege callouts (your own map pictures)

Tap **Callouts** on the deck (or **☰ > Callouts**) to see your map callout pictures on the iPad. Put them on the laptop in the `callouts` folder in this folder (the Callouts screen has a button that opens it): one folder per map, one picture per floor, for example `callouts\Bank\1 Basement.png`, `2 First floor.png`, `3 Second floor.png`, `4 Roof.png`. The number in front only sets the order. Pinch to zoom, drag to move, double-tap to see the whole floor. Draw on the map with the Pencil (or turn on **Draw with finger**) to plan a strat with your team; each floor keeps its own drawing until you tap **Clear**.

### Contexts: a profile that sets things up when you pick it

In **Edit**, tap **Profile**, then **When you switch to this profile, also do** and add steps. For example, a **Study** profile can start the focus timer and open OneNote, and a **Stream** profile can open OBS, switch to your Game scene and turn on the light. The steps run when you tap the profile (or a button that switches to it). They don't run when the buttons just follow the app in front, so a game coming to the front never sets anything off. A step that fails, like an app that isn't installed, is skipped and the rest still run. Steps can also be **Focus timer** (`start`, `stop`, `pause`, `skip`) and **Screenshot**, in macros too.

### Quick capture

Tap **Capture** on the deck (or **☰ > Quick capture**) to save something without leaving what you're doing: write a note with the Pencil (or type it), take a photo with the iPad, or grab a **PC screenshot** of the screen your mouse is on. Everything goes into the `inbox` folder in this folder (or `inbox_folder` in settings.json): notes in `notes.md`, newest at the bottom, and the pictures next to it. Tap a saved note to put it on the PC's clipboard, or **Copy to PC** to send what you wrote without saving it. A macro step **Screenshot** does the same from a deck button.

### Arrange the deck like a widget board

Every button and live tile has a **Size** in its editor: Small, Wide, Tall, Big (2 by 2) or Huge (3 by 2). Big tiles show their numbers bigger. In **Edit**, drag a button or tile onto another to move it there. Gaps are filled automatically, so a profile can be a dashboard: a big FPS tile, CPU and GPU next to it, Now playing across the bottom.

## Video mode (smoother scrolling and dragging)

While things move, the picture is sent as **H.264 video**, encoded by the laptop's graphics card (NVIDIA, AMD or Intel) and decoded by the iPad's video chip. That needs far less data than pictures, so scrolling, dragging windows and videos stay smooth. Typing and small changes are still sent as sharp pictures straight away, and once things stop moving, the moved areas are re-sent at full sharpness, exactly as before.

- It's on by default. Turn it off or on with **☰ > Video mode** to compare.
- The laptop window says which encoder it uses, for example `[video] H.264 encoding on the NVIDIA GPU`. The `[speed]` line shows how many updates per second are video.
- start.bat installs the `av` package for this. If it isn't installed, or the iPad can't decode it, everything works as before without video.

## PC sound on the iPad

Whatever the laptop plays also comes out of the iPad's speakers, about 50 ms behind. Turn it on or off with **☰ > PC sound**.
- It starts by itself in the iPad Display app. In Safari, tap the screen once first.
- The laptop keeps playing the sound too. Turn the laptop's volume down if you only want it on the iPad. If the iPad goes quiet when the laptop is muted, leave the laptop at a low volume instead.
- start.bat installs the small `soundcard` package for this. If the menu says "not available", run start.bat again with an internet connection.

## Start automatically (optional, once)

Run **`autostart-setup.bat`** once (after start.bat has run at least once). From then on the host runs in the background whenever you log in to Windows. There's no window, so you never have to open start.bat: **just plug in the iPad and open the app.**

- If you also ran `display-setup.bat`, the virtual screen turns **on when you plug the iPad in** and **off when you unplug it** (it stays off while a profile like Gaming doesn't use the screen).
- The host's messages, including the `[speed]` lines, go to **`host.log`** in this folder.
- If Windows Firewall asks about "pythonw", allow it on Private networks. It only matters for Wi-Fi.
- To turn auto-start off, run `autostart-setup.bat remove`. That also stops the background host.

## Controls on the iPad

| Do this | Laptop gets |
|---|---|
| Tap | Left click (tap twice quickly for double-click) |
| Drag one finger | Click and drag (move windows, select text) |
| Press and hold | Right click |
| Two-finger tap | Right click |
| Two-finger drag | Scroll |
| Pinch | Zoom in on the iPad (☰ > Fit whole screen to reset). Taps still land exactly. |
| ☰ > Mirror laptop / Extend | Show a copy of the laptop screen, or use the iPad as its own screen |
| Three-finger swipe left / right | Throw the active window to the next screen |
| Three-finger swipe up / down | All windows (Task view) / show the desktop |
| Apple Pencil | A real Windows pen with pressure and tilt (☰ > Pencil pressure turns it into a plain mouse) |
| Magic Keyboard trackpad | Mouse, and two-finger scrolling |
| ☰ button (top left) | Keyboard, Esc / Tab / Del, Copy / Paste / Undo, quality, disconnect |

With a Magic Keyboard or Bluetooth keyboard, open the keyboard from the ☰ menu once and type normally. Cmd+C/V/Z act as Ctrl+C/V/Z.

## Speed and picture quality

The stream is tuned for low delay *and* a sharp picture:

- **Only changed parts are sent.** Typing in one window sends just that small patch, not the whole screen.
- **Small changes are sent sharp straight away.** That covers typing, menus and hover effects.
- **Big changes go out lighter and then sharpen.** While you scroll or a video plays, frames are lighter so they keep up. About 0.1 s after things stop moving, those areas are re-sent at high quality with full colour detail.
- **The mouse pointer is drawn on the iPad.** It moves instantly, and it jumps to your finger the moment you touch.
- **It adapts to your Wi-Fi.** On a slow link it lightens frames automatically, and quality comes back when the link speeds up.
- Full-screen updates are encoded on several CPU cores at once.

Quality modes (in the ☰ menu or on the connect screen):
- **Balanced** (default) gives low delay and a crisp picture once things settle.
- **Fast** gives the lowest delay and lighter frames. For the lowest delay of all, also set the PC to the "Fastest" resolution above.
- **Sharp** gives the best picture everywhere. It needs good Wi-Fi and is ideal for reading or drawing.
- **Wired** gives full quality with full colour detail and the fastest sharpening. It's for a cable connection, and it's picked automatically when you connect by cable (see WIRED.md).

The ☰ menu shows live stats: updates per second, Mbit/s and decode time.

## Pencil, touch and drawing-tablet mode

In **Settings > Pencil and touch**:
- **Pressure** sets how firm a stroke has to be, with a curve from softer to
  firmer. "Always the same" gives every stroke the same pressure.
  (The USB-C Apple Pencil doesn't sense pressure, so this matters for
  pressure-sensing Pencils.)
- **Ignore your hand while drawing** stops a resting palm from clicking
  while the Pencil is on or near the screen.
- **Pencil buttons** adds buttons at the left edge: right-click (the next
  stroke is a right-click), eraser, undo and redo.
- **One finger: only scrolls** turns off finger clicks, so only the Pencil
  clicks and draws.
- **Gestures** lets you pick what each three- and four-finger swipe and
  tap does, from a list or as any key combination.

**Drawing tablet** (in the menu) turns the iPad into a pad for one PC
screen, like a Wacom tablet. Nothing is streamed, so it costs the PC almost
nothing. Pick the screen under "Drawing tablet area". Pinch to zoom in on a
part of the screen for fine work.

## Several monitors

The iPad shows its own virtual screen by default, whichever monitors are
plugged in and whether the laptop lid is open or closed. Windows renumbers
screens when that changes, so the app remembers a screen by what it is
(the iPad's, the laptop's, or a named monitor), not by its number. A gaming
monitor is never picked by accident. Screen capture is capped at the iPad's
60 frames a second, so a 144 or 240 Hz monitor costs no more than a 60 Hz one.

## Encrypted Wi-Fi, more iPads, iPhone and remote access

- **Encrypted Wi-Fi:** the PC's window also shows an `https://…:8766`
  address and a **pairing code**. Add that address in the app's PC list.
  The first time you connect, the app shows the code: check it matches,
  and from then on the connection is encrypted and the iPad only talks to
  that PC. Under Settings > Connection you can switch off the unencrypted
  address for Wi-Fi. The USB-C cable is never affected.
- **More than one iPad:** set the Virtual Display Driver to two (or more)
  monitors. Each iPad then gets its own screen automatically.
- **iPhone:** open the PC's Wi-Fi address in Safari on the iPhone (or add
  it in the app). It opens as a button deck and never streams the screen.
- **From anywhere:** see [REMOTE.md](REMOTE.md) (Tailscale, about 5 minutes).
- **Recording:** in the iPad menu, tap **Record the iPad screen**. A red dot
  shows while it records. Tap it again to stop, and the video is saved in the
  **recordings** folder on the PC. It reuses the stream's captured frames
  and encodes on the graphics card, so it adds little load.

## Self-test

With start.bat running and the iPad showing the screen, run
**selftest.bat**. In about two minutes it:
- lists the PC's parts and screens;
- measures capture and video encoding;
- streams to a test client to check frame rate, smoothness and what it
  costs the PC;
- runs a stress test.

The results are saved in selftest-report.txt. It never presses keys or
changes settings, but windows of moving colours flash on the iPad while
it runs.

## Troubleshooting

- **Safari can't connect.** Make sure both devices are on the same Wi-Fi and the host window is open. Allow Python through the firewall: Windows Security > Firewall & network protection > Allow an app through firewall, and tick Python for Private (and Public if your Wi-Fi is set to Public).
- **Campus or public Wi-Fi.** University networks usually block devices from talking to each other. Use the laptop's hotspot instead: **Settings > Network & internet > Mobile hotspot**. Join it from the iPad and use the address the host window shows. This is usually faster too.
- **Only "Screen 1" appears.** The virtual monitor isn't active. Check Display settings shows it set to Extend, then restart `start.bat`.
- **Black bars around the picture.** The PC resolution isn't your iPad's shape. Use the table above.
- **Clicks land in the wrong place.** Restart `start.bat` after changing resolution or scale.
- **New PIN:** run `start.bat --new-pin`.
- **Stopping:** close the host window. To remove the virtual monitor, uninstall it from VDC, or disable it in Device Manager > Display adapters.

## Good to know

- It's great for notes, docs, Discord, reference material and video. Fast competitive gaming isn't a good fit, because Wi-Fi always adds some delay.
- Only someone with the PIN can see or control your screen, but the stream isn't encrypted. Use your home Wi-Fi or the laptop's hotspot, not shared public networks.

## Free alternative

**spacedesk** (free for personal use) does the same job with its own driver and iPad app.
