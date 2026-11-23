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

- If you also ran `display-setup.bat`, the virtual screen turns **on when you plug the iPad in** and **off when you unplug it**.
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
| Apple Pencil | Precise mouse (hover moves the pointer on iPads that support it) |
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
