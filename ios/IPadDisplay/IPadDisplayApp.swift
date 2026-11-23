import SwiftUI
import UIKit
import WebKit

@main
struct IPadDisplayApp: App {
    @Environment(\.scenePhase) private var phase

    var body: some Scene {
        WindowGroup {
            ViewerView()
                .ignoresSafeArea()
                .statusBarHidden(true)
                .persistentSystemOverlays(.hidden)      // hide the home bar
                .defersSystemGestures(on: .all)         // edge swipes go to the PC first
                .background(Color.black)
        }
        .onChange(of: phase) { newPhase in
            if newPhase == .active { Viewer.shared.becameActive() } else { Viewer.shared.setDim(false) }
        }
    }
}

struct ViewerView: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView { Viewer.shared.webView }
    func updateUIView(_ uiView: WKWebView, context: Context) {}
}

/// Owns the relay and the full-screen web view that shows the PC's screen.
final class Viewer: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
    static let shared = Viewer()

    let relay = Relay()
    private(set) var webView: WKWebView!
    private var showingWaiting = false
    private var retry: DispatchWorkItem?
    private let usbURL = URL(string: "http://127.0.0.1:\(Relay.viewerPort)/")!
    /// What the web view is showing or trying: the cable, or a PC over Wi-Fi.
    private var current: URL?
    /// The PC list is open: don't connect anywhere until something is picked.
    private var picking = false
    /// A Wi-Fi PC was picked by hand: stay on it even if a cable PC appears.
    private var wifiChosen = false
    private let defaults = UserDefaults.standard
    /// Last iPad clipboard state we know about, so we only send real changes.
    private var lastPasteboard = UIPasteboard.general.changeCount
    /// Brightness before the page dimmed the screen (the PC went to sleep), if dimmed.
    private var brightnessBeforeDim: CGFloat?

    override init() {
        super.init()
        let config = WKWebViewConfiguration()
        config.applicationNameForUserAgent = "iPadDisplayApp"   // lets the page know it's in the app
        config.allowsInlineMediaPlayback = true
        config.suppressesIncrementalRendering = false
        config.mediaTypesRequiringUserActionForPlayback = []    // PC sound can start by itself
        config.userContentController.add(self, name: "clip")   // shared clipboard (PC -> iPad)
        config.userContentController.add(self, name: "screen") // dim while the PC sleeps
        config.userContentController.add(self, name: "pc")     // the PC list (Wi-Fi, second PC)
        let web = WKWebView(frame: .zero, configuration: config)
        web.navigationDelegate = self
        web.isOpaque = false
        web.backgroundColor = .black
        web.scrollView.backgroundColor = .black
        web.scrollView.isScrollEnabled = false
        web.scrollView.bounces = false
        web.scrollView.contentInsetAdjustmentBehavior = .never
        web.allowsBackForwardNavigationGestures = false
        web.allowsLinkPreview = false
        webView = web
        relay.start()
        relay.onTunnel = { [weak self] in self?.cableAppeared() }
        NotificationCenter.default.addObserver(forName: UIPasteboard.changedNotification,
                                               object: nil, queue: .main) { [weak self] _ in
            self?.sendPasteboardIfChanged()
        }
        UIDevice.current.isBatteryMonitoringEnabled = true      // for the status corner
        for name in [UIDevice.batteryLevelDidChangeNotification, UIDevice.batteryStateDidChangeNotification] {
            NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                self?.sendBattery()
            }
        }
        showWaiting()
        scheduleLoad(after: 1.5)          // give the laptop a moment to open its cable tunnels
    }

    func becameActive() {
        UIApplication.shared.isIdleTimerDisabled = true      // keep the screen on
        relay.reset()
        sendPasteboardIfChanged()                            // copied in another app?
        if showingWaiting { scheduleLoad(after: 0.8) }
        // Otherwise the page reconnects by itself once fresh tunnels arrive.
    }

    private func scheduleLoad(after seconds: Double) {
        retry?.cancel()
        guard !picking else { return }
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.picking else { return }
            self.load(self.nextURL())
        }
        retry = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    private func load(_ url: URL) {
        current = url
        webView.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 6))
    }

    /// The cable when a laptop is plugged in, otherwise the Wi-Fi PC (if set up).
    private func nextURL() -> URL {
        if wifiChosen, let w = wifiURL { return w }
        if relay.hasTunnel() { return usbURL }
        if autoWifi, let w = wifiURL { return w }
        return usbURL                     // waits for the cable
    }

    /// A laptop opened a cable tunnel: use it if we're waiting, or on Wi-Fi only as a fallback.
    private func cableAppeared() {
        guard !picking else { return }
        if showingWaiting || (current != usbURL && !wifiChosen) {
            retry?.cancel()
            load(usbURL)
        }
    }

    // MARK: Saved PCs (Wi-Fi)

    private var pcs: [[String: String]] {
        get { defaults.array(forKey: "pcs") as? [[String: String]] ?? [] }
        set { defaults.set(newValue, forKey: "pcs") }
    }
    private var wifiURL: URL? { defaults.string(forKey: "lastWifi").flatMap(URL.init(string:)) }
    private var autoWifi: Bool { defaults.object(forKey: "autoWifi") as? Bool ?? true }

    /// "192.168.1.20" -> "http://192.168.1.20:8765/"
    private static func normalize(_ raw: String) -> String? {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if !s.contains("://") { s = "http://" + s }
        guard var c = URLComponents(string: s), let host = c.host, !host.isEmpty else { return nil }
        if c.port == nil { c.port = 8765 }
        c.path = "/"
        c.query = nil
        return c.string
    }

    private func handlePC(_ body: [String: Any]) {
        if let target = body["connect"] as? String {
            picking = false
            if target == "usb" {
                wifiChosen = false
                load(usbURL)
            } else if let url = URL(string: target) {
                defaults.set(target, forKey: "lastWifi")
                wifiChosen = true
                load(url)
            }
        } else if let raw = body["add"] as? String, let url = Viewer.normalize(raw) {
            var list = pcs.filter { $0["url"] != url }
            let name = (body["name"] as? String)?.trimmingCharacters(in: .whitespaces) ?? ""
            list.append(["url": url, "name": name.isEmpty ? (URL(string: url)?.host ?? url) : name])
            pcs = list
            showWaiting()
        } else if let url = body["remove"] as? String {
            pcs = pcs.filter { $0["url"] != url }
            if defaults.string(forKey: "lastWifi") == url { defaults.removeObject(forKey: "lastWifi") }
            showWaiting()
        } else if let auto = body["auto"] as? Bool {
            defaults.set(auto, forKey: "autoWifi")
        } else if body["pick"] != nil {          // "Switch PC" in the page's menu
            picking = true
            retry?.cancel()
            showWaiting()
        }
    }

    // MARK: Shared clipboard

    /// The page passes on something copied on the PC: put it on the iPad clipboard.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "screen", let body = message.body as? [String: Any] {
            setDim(body["dim"] as? Bool ?? false)
            return
        }
        if message.name == "pc", let body = message.body as? [String: Any] {
            handlePC(body)
            return
        }
        guard message.name == "clip", let body = message.body as? [String: Any] else { return }
        let pb = UIPasteboard.general
        if let b64 = body["png"] as? String, let data = Data(base64Encoded: b64), let image = UIImage(data: data) {
            pb.image = image
        } else if let text = body["text"] as? String {
            pb.string = text
        } else {
            return
        }
        lastPasteboard = pb.changeCount
    }

    /// Something new was copied on the iPad: send it to the PC clipboard.
    private func sendPasteboardIfChanged() {
        let pb = UIPasteboard.general
        guard pb.changeCount != lastPasteboard, !showingWaiting else { return }
        lastPasteboard = pb.changeCount
        var payload: [String: String] = [:]
        if pb.hasImages, let image = pb.image, let png = image.pngData(), png.count <= 8_000_000 {
            payload["png"] = png.base64EncodedString()
        } else if pb.hasStrings, let text = pb.string {
            payload["text"] = text
        } else {
            return
        }
        guard let json = try? JSONSerialization.data(withJSONObject: payload),
              let arg = String(data: json, encoding: .utf8) else { return }
        webView.evaluateJavaScript("window.ipadClip && window.ipadClip(\(arg))", completionHandler: nil)
    }

    // MARK: Battery and brightness

    /// Tell the page the iPad's battery (web pages can't read it themselves).
    private func sendBattery() {
        let device = UIDevice.current
        guard !showingWaiting, device.batteryLevel >= 0 else { return }
        let charging = device.batteryState == .charging || device.batteryState == .full
        webView.evaluateJavaScript("window.ipadBattery && window.ipadBattery(\(device.batteryLevel), \(charging))",
                                   completionHandler: nil)
    }

    /// Dim the screen while the PC is asleep or off, and put it back after.
    func setDim(_ dim: Bool) {
        let screen = webView.window?.windowScene?.screen ?? UIScreen.main
        if dim, brightnessBeforeDim == nil {
            brightnessBeforeDim = screen.brightness
            screen.brightness = 0.02
        } else if !dim, let before = brightnessBeforeDim {
            screen.brightness = before
            brightnessBeforeDim = nil
        }
    }

    // MARK: WKNavigationDelegate

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        if let scheme = webView.url?.scheme, scheme.hasPrefix("http") {
            showingWaiting = false
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.sendBattery() }
        }
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        if !showingWaiting { showWaiting() }
        scheduleLoad(after: 2)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        if !showingWaiting { showWaiting() }
        scheduleLoad(after: 2)
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) {
        scheduleLoad(after: 0.5)
    }

    private func showWaiting() {
        showingWaiting = true
        current = nil
        let state: [String: Any] = ["pcs": pcs, "last": defaults.string(forKey: "lastWifi") ?? "",
                                    "auto": autoWifi, "picking": picking || wifiChosen]
        let json = (try? JSONSerialization.data(withJSONObject: state)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        webView.loadHTMLString(Viewer.waitingHTML.replacingOccurrences(of: "/*STATE*/{}", with: json), baseURL: nil)
    }

    private static let waitingHTML = """
    <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      html,body{margin:0;min-height:100%;background:#000;color:#e8e8e8;font:17px -apple-system,sans-serif}
      .w{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box}
      .c{width:100%;max-width:520px;background:#15171c;border:1px solid #2a2e37;border-radius:18px;padding:28px;box-sizing:border-box}
      h1{font-size:22px;margin:0 0 6px} p{color:#9aa3b2;margin:0 0 18px;font-size:15px}
      h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:#8a93a3;margin:22px 0 8px}
      ol{margin:0;padding-left:22px;line-height:1.7} b{color:#fff}
      .s{margin-top:14px;color:#7d8697;font-size:14px}
      .d{display:inline-block;width:8px;height:8px;border-radius:4px;background:#4f8cff;margin-right:8px;animation:p 1.2s infinite}
      @keyframes p{50%{opacity:.2}}
      .pc{display:flex;align-items:center;gap:8px;padding:8px 0;border-bottom:1px solid #22262e}
      .pc .n{flex:1;min-width:0} .pc .n small{display:block;color:#7d8697;font-size:13px}
      button,input{font:inherit;border-radius:10px;border:1px solid #2f3440;background:#0d0f13;color:#eee;padding:10px 14px}
      button.go{background:#4f8cff;border:0;color:#fff;font-weight:600}
      .add{display:flex;gap:8px;margin-top:10px} .add input{flex:1;min-width:0}
      label.t{display:flex;gap:10px;align-items:center;margin-top:12px;color:#9aa3b2;font-size:14px}
    </style></head><body><div class="w"><div class="c">
      <h1>iPad Display</h1><p id="sub">Waiting for your PC over USB-C…</p>
      <ol>
        <li>Plug this iPad into the laptop with a <b>USB-C cable</b>.</li>
        <li>If the iPad asks, tap <b>Trust</b> and enter your passcode.</li>
        <li>On the laptop, run <b>start.bat</b> and keep its window open.</li>
      </ol>
      <div class="s" id="auto"><span class="d"></span>This connects automatically.</div>
      <h2 id="h">Or over Wi-Fi</h2>
      <div id="list"></div>
      <div class="add"><input id="addr" placeholder="PC address, e.g. 192.168.1.20" autocapitalize="off" autocorrect="off" inputmode="url"><button id="addbtn">Add</button></div>
      <div class="s">The address is in the PC's start.bat window, next to "(Wi-Fi)". The PC asks for its PIN the first time.</div>
      <label class="t"><input type="checkbox" id="autowifi"> Use the last Wi-Fi PC when the cable isn't plugged in</label>
    </div></div>
    <script>
      const S = /*STATE*/{};
      const post = m => window.webkit.messageHandlers.pc.postMessage(m);
      const list = document.getElementById('list');
      function row(name, sub, go, remove) {
        const r = document.createElement('div'); r.className = 'pc';
        const n = document.createElement('div'); n.className = 'n';
        n.textContent = name; const sm = document.createElement('small'); sm.textContent = sub; n.appendChild(sm);
        const b = document.createElement('button'); b.className = 'go'; b.textContent = 'Connect'; b.onclick = go;
        r.append(n, b);
        if (remove) { const x = document.createElement('button'); x.textContent = '✕'; x.onclick = remove; r.appendChild(x); }
        list.appendChild(r);
      }
      if (S.picking) {
        document.getElementById('sub').textContent = 'Pick a PC.';
        document.getElementById('auto').style.display = 'none';
        document.getElementById('h').textContent = 'Your PCs';
        row('USB-C cable', 'The laptop plugged into this iPad', () => post({ connect: 'usb' }));
      }
      (S.pcs || []).forEach(p => row(p.name, p.url.replace('http://', '').replace(/\\/$/, '') + (p.url === S.last ? ' · last used' : ''),
        () => post({ connect: p.url }), () => post({ remove: p.url })));
      if (!(S.pcs || []).length) { const e = document.createElement('div'); e.className = 's'; e.textContent = 'No Wi-Fi PCs yet.'; list.appendChild(e); }
      document.getElementById('addbtn').onclick = () => { const v = document.getElementById('addr').value; if (v.trim()) post({ add: v }); };
      const a = document.getElementById('autowifi'); a.checked = S.auto !== false; a.onchange = () => post({ auto: a.checked });
    </script>
    </body></html>
    """
}
