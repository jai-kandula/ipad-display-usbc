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
Sideloadly needs the versions of **iTunes** and **iCloud** from **Apple's website**, not the Microsoft Store ones. Sideloadly's page links to both.
- If you already have the Microsoft Store versions of iTunes or "Apple Devices", uninstall them first.
- These also install "Apple Mobile Device Service", which the cable connection uses.

### 2. Install Sideloadly
Download it from **https://sideloadly.io** and install it.

### 3. Download the app
Open **https://github.com/jai-kandula/ipad-display-usbc/releases** and find **"iPad Display app (latest build)"**. Download **`IPadDisplay.ipa`**.

### 4. Install the app on the iPad
1. Plug the iPad into the laptop and unlock it. If it asks **"Trust This Computer?"**, tap **Trust** and enter your passcode.
2. Open Sideloadly. Your iPad should appear in the **iDevice** box.
3. Drag **IPadDisplay.ipa** onto Sideloadly.
4. Type your **Apple ID** email and click **Start**. Enter your Apple ID password and any 2-factor code when asked. Sideloadly sends these only to Apple, to sign the app. You can use a spare Apple ID if you prefer.
5. Wait for **"Done."**

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

Controls are the same as before: tap, drag, hold for right click, two-finger scroll, Apple Pencil, and the ☰ menu.

## Shared clipboard

Copy something on the laptop (text or a picture) and paste it on the iPad, or the other way round. It works while the iPad Display app is connected.
- Laptop to iPad: copy as usual. It's on the iPad clipboard within half a second.
- iPad to laptop: copy in any iPad app, then switch back to iPad Display. The first time, iPadOS asks **"Allow Paste"**. To stop it asking, go to **Settings > Apps > iPad Display > Paste from Other Apps** and choose **Allow**.
- The laptop window shows `[clipboard] PC -> iPad` or `iPad -> PC` each time.
- This needs the newest iPad Display app, so install the latest `IPadDisplay.ipa` once.

## Turn the iPad screen on and off automatically (optional, once)

Normally the virtual screen stays plugged in to Windows even when the iPad isn't, so windows and your mouse can get lost on it. To have it appear only while you use the iPad:

1. Double-click **`display-setup.bat`** in this folder and click **Yes** on the Windows admin prompt. It turns the screen off and on once as a test.
2. From now on, **`start.bat` turns the virtual screen on**, and **closing the start.bat window turns it off** (the X button or Ctrl+C both work). Windows remembers its resolution and position, and moves any windows on it back to your laptop screen.

It also switches off when the laptop restarts. If you move this folder, run `display-setup.bat` again. To undo it, run `display-setup.bat remove`. To leave the screen alone for one session, run `start.bat --keep-screen`. If it doesn't switch, look in `vdd.log` in this folder.

## Every 7 days (free Apple ID)

When the app stops opening, plug in the iPad, open Sideloadly, drag `IPadDisplay.ipa` in again, and click Start. Your settings stay. Sideloadly also has an **automatic refresh** option that can do this for you while the laptop and iPad are connected.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| Laptop window says `Apple's USB service isn't running` | Install iTunes from Apple's website (step 1), then restart `start.bat`. |
| Laptop window never says `iPad plugged in` | Unlock the iPad and tap Trust if asked. Try another cable or port, because some cables only charge. |
| `waiting for the iPad Display app to be opened` | Open the app on the iPad and keep it on screen. |
| App says "Waiting for your PC over USB-C…" | Make sure `start.bat` is running. It connects within a few seconds. |
| App won't open: "Untrusted Developer" | Do step 5 (VPN & Device Management > Trust). |
| App won't open after a week | Re-install with Sideloadly (see above). |
| Sideloadly can't see the iPad | Use iTunes from Apple's website, not the Microsoft Store version. Replug and unlock the iPad. |

**Speed:** your iPad (A16) has a USB 2.0 port, which still moves far more data than this needs. Wired mode at 2360 × 1640 uses about 20 to 25 Mbit/s.
