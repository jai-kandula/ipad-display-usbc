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
    private let serverURL = URL(string: "http://127.0.0.1:\(Relay.viewerPort)/")!
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
        scheduleLoad(after: 0.5)
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
        let work = DispatchWorkItem { [weak self] in
            guard let self else { return }
            self.webView.load(URLRequest(url: self.serverURL,
                                         cachePolicy: .reloadIgnoringLocalCacheData,
                                         timeoutInterval: 6))
        }
        retry = work
        DispatchQueue.main.asyncAfter(deadline: .now() + seconds, execute: work)
    }

    // MARK: Shared clipboard

    /// The page passes on something copied on the PC: put it on the iPad clipboard.
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        if message.name == "screen", let body = message.body as? [String: Any] {
            setDim(body["dim"] as? Bool ?? false)
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
        if webView.url?.host == "127.0.0.1" {
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
        webView.loadHTMLString(Viewer.waitingHTML, baseURL: nil)
    }

    private static let waitingHTML = """
    <!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      html,body{margin:0;height:100%;background:#000;color:#e8e8e8;font:17px -apple-system,sans-serif}
      .w{height:100%;display:flex;align-items:center;justify-content:center;padding:24px;box-sizing:border-box}
      .c{max-width:520px;background:#15171c;border:1px solid #2a2e37;border-radius:18px;padding:28px}
      h1{font-size:22px;margin:0 0 6px} p{color:#9aa3b2;margin:0 0 18px;font-size:15px}
      ol{margin:0;padding-left:22px;line-height:1.7} b{color:#fff}
      .s{margin-top:18px;color:#7d8697;font-size:14px}
      .d{display:inline-block;width:8px;height:8px;border-radius:4px;background:#4f8cff;margin-right:8px;animation:p 1.2s infinite}
      @keyframes p{50%{opacity:.2}}
    </style></head><body><div class="w"><div class="c">
      <h1>iPad Display</h1><p>Waiting for your PC over USB-C…</p>
      <ol>
        <li>Plug this iPad into the laptop with a <b>USB-C cable</b>.</li>
        <li>If the iPad asks, tap <b>Trust</b> and enter your passcode.</li>
        <li>On the laptop, run <b>start.bat</b> and keep its window open.</li>
      </ol>
      <div class="s"><span class="d"></span>This connects automatically.</div>
    </div></div></body></html>
    """
}
