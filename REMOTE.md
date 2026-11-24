# Use the iPad from anywhere (Tailscale)

Tailscale puts the PC and the iPad on a private, encrypted network that works
from any Wi-Fi or mobile data. It is free for personal use. There are no
router settings to change and no ports to open.

## Once (about 5 minutes)

1. **On the PC:** install Tailscale from https://tailscale.com/download and
   sign in. Google, Microsoft or GitHub accounts all work.
2. **On the iPad:** install **Tailscale** from the App Store and sign in with
   the **same account**. Allow the VPN when iPadOS asks.
3. On the PC, click the Tailscale icon next to the clock. Find this PC's
   address: it starts with **100.**, for example 100.101.102.103.
4. In the iPad Display app, open the PC list and add
   `https://100.101.102.103` (use your own address). The app fills in the
   encrypted port by itself.
5. The first time it connects, the app shows a **pairing code**. Check that
   it is the same as the code in the PC's window, then tap **It matches**.

## Every time

Keep the PC on with start.bat running, or set up auto-start. Turn
Tailscale on on the iPad, open the app and pick the PC from the list.

## Good to know

- Away from home the picture depends on both internet connections. Pick
  **Fast** or **Balanced** under Settings > Picture.
- Tailscale already encrypts everything, and the encrypted address adds a
  second layer, so it is safe on public Wi-Fi.
- To stop remote access, sign out of Tailscale on the PC.
