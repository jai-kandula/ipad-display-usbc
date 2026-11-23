# USB-C cable mode (native iPad app)

Plug the iPad into the laptop with a **USB-C to USB-C cable**. The iPad Display app shows your second screen over the cable, with no Wi-Fi and no PIN, in full-quality "Wired" mode.

You'll need:
- the cable you charge the iPad with (USB-C to USB-C, or USB-C to USB-A)
- the laptop set up as in the README: Python 3.15, `start.bat`, and **one** virtual screen from Virtual Display Driver
- a free Apple ID

## Why an app this time?

iPadOS only lets apps, not web pages, send data over the USB cable. This app is a small wrapper: it shows the same screen and controls as the Safari version, but talks to the laptop through the cable.

Apple only lets you install apps outside the App Store with a "sideloading" tool. With a free Apple ID, the app has to be **re-installed every 7 days**. It takes about a minute and keeps your settings. A paid Apple Developer account ($99/year) makes it last a year.

---

## One-time setup (about 15 minutes)

### 1. Install Apple's iPad drivers on the laptop
Install **iTunes from Apple's website** (https://apple.co/ms), not the Microsoft Store version.
- If you already have the Microsoft Store versions of iTunes or "Apple Devices", uninstall them first.
- This also installs "Apple Mobile Device Service", which the cable connection and iLoader both use.

### 2. Install iLoader
Download the Windows installer from **https://github.com/nab138/iloader/releases** (or **https://iloader.app**) and install it. Use version 2.4.1 or newer.

> Why not Sideloadly? Since October 2026 Apple rejects Sideloadly's free sign-in with error **-80046**. iLoader signs in a way Apple still accepts.

### 3. Download the app
Open **https://github.com/jai-kandula/ipad-display-usbc/releases** and find **"iPad Display app (latest build)"**. Download **`IPadDisplay.ipa`**.

### 4. Install the app on the iPad
1. Plug the iPad into the laptop and unlock it. If it asks **"Trust This Computer?"**, tap **Trust** and enter your passcode.
2. Open iLoader. Your iPad should show up at the top.
3. Sign in with your **Apple ID**, and enter the 2-factor code when asked. iLoader uses it only to get a free signing certificate from Apple. You can use a spare Apple ID if you prefer.
4. Choose **Import IPA** (install any app), pick **IPadDisplay.ipa**, and let it finish.

### 5. Allow the app on the iPad
1. Go to **Settings > Privacy & Security > Developer Mode** and turn it on. The iPad restarts. After the restart, tap **Turn On** and enter your passcode.
2. Go to **Settings > General > VPN & Device Management**. Tap your Apple ID under "Developer App", then tap **Trust**.

The **iPad Display** app is now on your home screen.

---

## Every time you use it

1. Plug the iPad into the laptop with the USB-C cable.
2. On the laptop, run **`start.bat`**. You should see `[usb] iPad plugged in over USB`.
3. On the iPad, open the **iPad Display** app. The laptop window shows `[usb] Connected to the iPad Display app over USB.`
4. The first time, check **Your iPad** shows **iPad (A16)**, pick the virtual screen (not "main"), and tap **Connect**. There's no PIN over the cable. Quality switches to **Wired** automatically.

After the first time it connects by itself whenever you open the app.

Controls are the same as before: tap, drag, hold for right click, two-finger scroll, Apple Pencil, and the ☰ menu. Also:
- **Pinch** with two fingers to zoom in on small text, and pinch out to go back. Tapping still clicks exactly where you tap. **☰ > Fit whole screen** resets the zoom.
- **☰ > Mirror laptop** makes the iPad show a copy of the laptop screen. **☰ > Extend** makes it a separate screen again. Windows may change the laptop's resolution while mirroring.
- **Three-finger swipe** left or right throws the window you last clicked to the next screen. Swipe up to see all windows, down to show the desktop.
- **Apple Pencil** works as a real Windows pen: pressure and tilt reach drawing and note apps such as OneNote, Krita and Photoshop. Turn it off with **☰ > Pencil pressure** to use the Pencil as a plain mouse. (The USB-C Apple Pencil has no pressure, but tilt still works.)
- **Magic Keyboard trackpad**: two-finger scrolling scrolls on the laptop, and clicking works as a mouse.

## Shared clipboard

Copy something on the laptop (text or a picture) and paste it on the iPad, or the other way round. It works while the iPad Display app is connected.
- Laptop to iPad: copy as usual. It's on the iPad clipboard within half a second.
- iPad to laptop: copy in any iPad app, then switch back to iPad Display. The first time, iPadOS asks **"Allow Paste"**. To stop it asking, go to **Settings > Apps > iPad Display > Paste from Other Apps** and choose **Allow**.
- The laptop window shows `[clipboard] PC -> iPad` or `iPad -> PC` each time.
- This needs the newest iPad Display app, so install the latest `IPadDisplay.ipa` once.

## PC sound on the iPad

Whatever the laptop plays also comes out of the iPad's speakers, about 50 ms behind. Turn it on or off with **☰ > PC sound**.
- It starts by itself in the iPad Display app. In Safari, tap the screen once first.
- The laptop keeps playing the sound too. Turn the laptop's volume down if you only want it on the iPad. If the iPad goes quiet when the laptop is muted, leave the laptop at a low volume instead.
- start.bat installs the small `soundcard` package for this. If the menu says "not available", run start.bat again with an internet connection.

## Portrait, screen size and text size

In **☰ > Screen** you can change the iPad's screen in Windows without opening Windows settings:

- **Sharp**: the iPad's full resolution at 200% text size. Pixel-perfect, with text the size you'd expect on an iPad.
- **Roomy**: full resolution at 150%. Still sharp, with smaller text and more room.
- **Max space**: full resolution at 100%. Tiny text, the most room.
- **Fast**: half the resolution at 100%. A quarter of the pixels to send, so it's the quickest, and a little softer. It's faded out if the Virtual Display Driver doesn't offer that resolution; add it in the driver's settings to use it.
- **Turn with the iPad**: hold the iPad upright and the screen turns portrait in Windows; turn it back and it goes landscape again.

Only the iPad's screen is changed. The laptop's main screen is always left alone.

## Profiles and the button deck (Work and Gaming)

A **profile** says what the iPad is for right now. Switch with **☰ > Profile**, or the buttons along the top of the deck. Two come ready to use:

- **💼 Work**: the iPad is your second screen. A panel of shortcut buttons (copy, paste, screenshot, task view and more) slides out when you tap the small **‹** tab on the right edge.
- **🎮 Gaming**: the whole iPad becomes a grid of big buttons, like a Stream Deck: mute mic, deafen, save a clip, screenshot, volume and music. **Nothing is streamed**, and if you ran `display-setup.bat` the iPad screen is also removed from Windows, so the game runs as if the iPad weren't there. A tap costs the laptop about as much as pressing a key.

To change things, tap **Edit** on the deck (or **☰ > Edit buttons and profiles**):

- Tap a button to change it, or **+ Add button** for a new one. A button can **press keys** (like `ctrl+shift+m`, `win+d`, `alt+f10`, `f13`, `playpause`, `volup`), **type text**, **open** an app, file or link (`obs64.exe`, `steam://`, `https://…`), or **switch profile**. Use the ◀ ▶ buttons to move it.
- **⚙︎ Profile** sets the name, icon, buttons per row, and whether the profile shows the PC screen, the button panel, and keeps the iPad screen in Windows. **+ Profile** makes a new one (for example one per game).
- Tip: some games ignore keys that come from another device. Buttons work best when they press shortcuts for apps like Discord, OBS or NVIDIA. Give the app a spare shortcut such as `ctrl+alt+f13` and put the same one on the button.

Your profiles are saved on the laptop in `profiles.json` in this folder, so they're kept when you update the app.

## Video mode (smoother scrolling and dragging)

While things move, the picture is sent as **H.264 video**, encoded by the laptop's graphics card (NVIDIA, AMD or Intel) and decoded by the iPad's video chip. That needs far less data than pictures, so scrolling, dragging windows and videos stay smooth. Typing and small changes are still sent as sharp pictures straight away, and once things stop moving, the moved areas are re-sent at full sharpness, exactly as before.

- It's on by default. Turn it off or on with **☰ > Video mode** to compare.
- The laptop window says which encoder it uses, for example `[video] H.264 encoding on the NVIDIA GPU`. The `[speed]` line shows how many updates per second are video.
- start.bat installs the `av` package for this. If it isn't installed, or the iPad can't decode it, everything works as before without video.

## Turn the iPad screen on and off automatically (optional, once)

Normally the virtual screen stays plugged in to Windows even when the iPad isn't, so windows and your mouse can get lost on it. To have it appear only while you use the iPad:

1. Double-click **`display-setup.bat`** in this folder and click **Yes** on the Windows admin prompt. It turns the screen off and on once as a test.
2. From now on, **`start.bat` turns the virtual screen on**, and **closing the start.bat window turns it off** (the X button or Ctrl+C both work). Windows remembers its resolution and position, and moves any windows on it back to your laptop screen.

It also switches off when the laptop restarts. If you move this folder, run `display-setup.bat` again. To undo it, run `display-setup.bat remove`. To leave the screen alone for one session, run `start.bat --keep-screen`. If it doesn't switch, look in `vdd.log` in this folder.

## Start automatically (optional, once)

Run **`autostart-setup.bat`** once (after start.bat has run at least once). From then on the host runs in the background whenever you log in to Windows. There's no window, so you never have to open start.bat: **just plug in the iPad and open the app.**

- If you also ran `display-setup.bat`, the virtual screen turns **on when you plug the iPad in** and **off when you unplug it** (it stays off while a profile like Gaming doesn't use the screen).
- The host's messages, including the `[speed]` lines, go to **`host.log`** in this folder.
- If Windows Firewall asks about "pythonw", allow it on Private networks. It only matters for Wi-Fi.
- To turn auto-start off, run `autostart-setup.bat remove`. That also stops the background host.

## Every 7 days (free Apple ID)

Apps signed with a free Apple ID stop opening after 7 days. There are two ways to deal with that.

**Quick way:** plug in the iPad, open iLoader, and install `IPadDisplay.ipa` again with **Import IPA**. It takes about a minute and your settings stay.

**Automatic way (set up once):** in iLoader, choose **Install SideStore**. SideStore is a free app that re-signs your sideloaded apps on the iPad itself, so no laptop is needed. Then:
1. Open SideStore on the iPad and follow its first-run steps. It may ask you to install its small VPN helper app from the App Store.
2. In SideStore, tap **My Apps > +** and pick `IPadDisplay.ipa`. You can save the file to the iPad's Files app first, or download it in Safari from the Releases page.
3. SideStore then refreshes iPad Display in the background before it expires. If a refresh is ever missed, open SideStore and tap **Refresh All**.

A free Apple ID can have 3 sideloaded apps at a time, and SideStore counts as one of them.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| Laptop window says `Apple's USB service isn't running` | Install iTunes from Apple's website (step 1), then restart `start.bat`. |
| Laptop window never says `iPad plugged in` | Unlock the iPad and tap Trust if asked. Try another cable or port, because some cables only charge. |
| `waiting for the iPad Display app to be opened` | Open the app on the iPad and keep it on screen. |
| App says "Waiting for your PC over USB-C…" | Make sure `start.bat` is running. It connects within a few seconds. |
| App won't open: "Untrusted Developer" | Do step 5 (VPN & Device Management > Trust). |
| App won't open after a week | Re-install with iLoader, or refresh it in SideStore (see above). |
| iLoader can't see the iPad | Use iTunes from Apple's website, not the Microsoft Store version. Replug and unlock the iPad. |

**Speed:** your iPad (A16) has a USB 2.0 port, which still moves far more data than this needs. Wired mode at 2360 × 1640 uses about 20 to 25 Mbit/s.
