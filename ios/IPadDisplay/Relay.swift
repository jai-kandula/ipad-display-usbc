import Foundation
import Network

/// Connects the built-in viewer to the laptop over the USB-C cable.
///
/// The laptop (usb_bridge.py) opens TCP connections to port 8766 on this iPad
/// through Apple's USB service. Those arrive here as loopback connections and
/// wait as "spare tunnels". The viewer (a WKWebView) loads
/// http://127.0.0.1:8767/; each of its connections is paired with a spare
/// tunnel and bytes are copied both ways. To the web page it looks exactly like
/// talking to the laptop's web server, only over the cable.
final class Relay {
    static let tunnelPort: UInt16 = 8766
    static let viewerPort: UInt16 = 8767

    private let queue = DispatchQueue(label: "relay", qos: .userInteractive)
    private var idle: [NWConnection] = []        // laptop tunnels not in use yet
    private var waiting: [NWConnection] = []     // viewer connections waiting for a tunnel
    private var listeners: [NWListener] = []

    /// Number of tunnels ready right now (used to show "connected" state).
    private(set) var readyTunnels = 0

    func start() {
        queue.async { self.startListeners() }
    }

    /// Drop spare tunnels (e.g. after the app was in the background, when they
    /// are probably dead). The laptop notices and opens fresh ones.
    func reset() {
        queue.async {
            self.idle.forEach { $0.cancel() }
            self.idle.removeAll()
            self.readyTunnels = 0
            if self.listeners.isEmpty { self.startListeners() }
        }
    }

    // MARK: - Listening

    private func startListeners() {
        listeners.forEach { $0.cancel() }
        listeners.removeAll()
        do {
            listeners.append(try listen(Relay.tunnelPort) { [weak self] c in self?.gotTunnel(c) })
            listeners.append(try listen(Relay.viewerPort) { [weak self] c in self?.gotViewer(c) })
        } catch {
            NSLog("Relay: listen failed: \(error)")
            queue.asyncAfter(deadline: .now() + 1) { self.startListeners() }
        }
    }

    private func listen(_ port: UInt16, _ handler: @escaping (NWConnection) -> Void) throws -> NWListener {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        let params = NWParameters(tls: nil, tcp: tcp)
        params.requiredInterfaceType = .loopback      // only this iPad / the USB service
        params.allowLocalEndpointReuse = true
        let listener = try NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
        listener.newConnectionHandler = { [weak self] conn in
            guard let self else { return }
            conn.start(queue: self.queue)
            handler(conn)
        }
        listener.stateUpdateHandler = { [weak self] state in
            if case .failed = state {
                self?.queue.asyncAfter(deadline: .now() + 1) { self?.startListeners() }
            }
        }
        listener.start(queue: queue)
        return listener
    }

    // MARK: - Pairing

    private func gotTunnel(_ conn: NWConnection) {
        conn.stateUpdateHandler = { [weak self, weak conn] state in
            switch state {
            case .failed, .cancelled:
                guard let self, let conn else { return }
                self.idle.removeAll { $0 === conn }
                self.readyTunnels = self.idle.count
            default:
                break
            }
        }
        if !waiting.isEmpty {
            pair(viewer: waiting.removeFirst(), tunnel: conn)
        } else {
            idle.append(conn)
            readyTunnels = idle.count
        }
    }

    private func gotViewer(_ conn: NWConnection) {
        if !idle.isEmpty {
            let tunnel = idle.removeFirst()
            readyTunnels = idle.count
            pair(viewer: conn, tunnel: tunnel)
            return
        }
        waiting.append(conn)
        // No laptop yet: give up after a few seconds so the viewer can retry.
        queue.asyncAfter(deadline: .now() + 4) { [weak self, weak conn] in
            guard let self, let conn, let i = self.waiting.firstIndex(where: { $0 === conn }) else { return }
            self.waiting.remove(at: i)
            conn.cancel()
        }
    }

    private func pair(viewer: NWConnection, tunnel: NWConnection) {
        tunnel.stateUpdateHandler = nil
        pump(from: viewer, to: tunnel)
        pump(from: tunnel, to: viewer)
    }

    private func pump(from: NWConnection, to: NWConnection) {
        from.receive(minimumIncompleteLength: 1, maximumLength: 1 << 16) { [weak self] data, _, isComplete, error in
            if let data, !data.isEmpty {
                to.send(content: data, completion: .contentProcessed { sendError in
                    if sendError != nil {
                        from.cancel(); to.cancel()
                    } else if isComplete || error != nil {
                        from.cancel(); to.cancel()
                    } else {
                        self?.pump(from: from, to: to)
                    }
                })
            } else if isComplete || error != nil {
                from.cancel(); to.cancel()
            } else {
                self?.pump(from: from, to: to)
            }
        }
    }
}
