# Wired connection (cable from laptop to iPad)

A cable gives you the lowest delay and lets the app send the full-quality picture all the time.

## Why not just the USB charging cable?

iPadOS doesn't let a web page, or Windows, send data over the iPad's USB charging cable. Only native App Store apps can use that link. A web app *can* use a **network cable**, though. To the iPad, a wired network connection is just a very fast, private network.

There are two ways to do it. **Option A works with any USB-C iPad and is the one to use.**

---

## Option A: Ethernet cable (recommended)

### What you need (about $20 to $40 total)

| Item | Notes |
|---|---|
| **USB-C to Ethernet adapter for the iPad** | Get a **gigabit (1 Gbps)** one. One with a **USB-C power pass-through** port lets you charge the iPad at the same time, which is worth it. |
| **Ethernet port on the laptop** | If the laptop doesn't have one, get a second USB-C or USB-A to Ethernet adapter (gigabit). |
| **Ethernet cable** | Any short Cat 5e or Cat 6 cable. A normal cable works; you don't need a special "crossover" one. |

Older iPads with a Lightning port (like the iPad 9th gen) need Apple's **Lightning to USB 3 Camera Adapter** plus a USB Ethernet adapter.

### Setup (one time, about 5 minutes)

**1. Plug it in.** Connect laptop to adapter, adapter to cable, and cable to the iPad adapter.

**2. Give the laptop a fixed address**
1. Go to **Settings > Network & internet > Ethernet**. Pick the Ethernet adapter if there's more than one.
2. Next to **IP assignment**, click **Edit**, then choose **Manual** and turn **IPv4** on.
3. **IP address:** `192.168.77.1`
4. **Subnet mask:** `255.255.255.0`
5. Leave **Gateway** and **DNS** empty. Click **Save**.

**3. Give the iPad a fixed address**
1. On the iPad, open **Settings**. An **Ethernet** item appears when the adapter is plugged in. Tap it, then tap your adapter.
2. Tap **Configure IP** and choose **Manual**.
3. **IP Address:** `192.168.77.2`
4. **Subnet Mask:** `255.255.255.0`
5. Leave **Router** empty. Tap **Save**.

You can leave the iPad's Wi-Fi on. It will still reach the laptop through the cable and use Wi-Fi for the internet.

**4. Let the app through the firewall on the cable**
Windows treats a direct cable as an "Unidentified network" and blocks it by default. This allows the app on the cable only, and nowhere else:
1. Right-click Start and choose **Terminal (Admin)**.
2. Paste this and press Enter:
   ```
   New-NetFirewallRule -DisplayName "iPad Display (cable)" -Direction Inbound -Protocol TCP -LocalPort 8765 -RemoteAddress 192.168.77.0/24 -Action Allow -Profile Any
   ```

**5. Start it**
1. Close and reopen `start.bat`. The top address should be **`http://192.168.77.1:8765   <- cable (best)`**.
2. On the iPad, open Safari and go to **`http://192.168.77.1:8765`**.
3. The connect screen should say **"✓ Connected by cable"**, and **Wired** quality is picked automatically. Enter the PIN and connect.
4. Do **Share > Add to Home Screen** again. The old icon points to the Wi-Fi address, so delete it.

From then on: plug in the cable, run `start.bat`, and open the home-screen icon.

---

## Option B: USB cable with Personal Hotspot (cellular iPads only)

If your iPad has cellular (a SIM or eSIM) and your plan allows hotspot, the iPad can share a network with the laptop over a plain USB cable. **I haven't been able to test this one.** Option A is more reliable.

1. On the laptop, install **Apple Devices** from the Microsoft Store. It provides the USB network driver.
2. On the iPad, go to **Settings > Personal Hotspot** and turn on **Allow Others to Join**.
3. Plug the iPad into the laptop and tap **Trust** if asked.
4. Run `start.bat`. An address starting with `172.20.10.` should appear marked **cable**. Open that address in Safari on the iPad.

Heads-up: Windows may start using the iPad's cellular data for internet. Keep the laptop's Wi-Fi connected. If data use goes up, turn Personal Hotspot off when you're done.

---

## What "Wired" quality does

With the bandwidth a cable gives, Wired mode:
- sends almost everything at very high quality with **full colour detail (4:4:4)**, so text has no colour fringing;
- re-sharpens the rest within about 0.04 s, instead of about 0.12 s on Wi-Fi;
- uses the fixed 192.168.77.x link, so other devices on your Wi-Fi don't slow it down.

For a pixel-perfect picture, set the virtual screen to your iPad's **Sharpest** resolution from the README table.

## Troubleshooting

- **No "Ethernet" item in iPad Settings.** The adapter isn't recognised. Try another port or adapter. Some cheap adapters need power, so use one with power pass-through.
- **Safari can't open 192.168.77.1.**
  - Check both fixed addresses are exactly as above.
  - Check the firewall command ran without errors.
  - Make sure `start.bat` is running.
  - In Windows Terminal, `ping 192.168.77.2` should get replies.
- **Window shows no "cable" address.** The laptop's Ethernet isn't set to 192.168.77.1, or the cable isn't plugged in. Check Settings > Network & internet > Ethernet.
