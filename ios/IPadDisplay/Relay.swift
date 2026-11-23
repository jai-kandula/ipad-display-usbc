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
///
/// Every tunnel is read from the moment it arrives, so a tunnel the laptop has
/// closed (or recycled) is noticed and dropped right away instead of being
/// handed to the viewer later. The newest spare tunnel is always used first.
final class Relay {
    static let tunnelPort: UInt16 = 8766
    static let viewerPort: UInt16 = 8767
    private static let chunk = 1 << 20
    private static let maxSpare = 8

    /// One laptop tunnel and, once paired, the viewer connection it serves.
    private final class Tunnel {
        let conn: NWConnection
        var viewer: NWConnection?
        var closed = false
        init(_ conn: NWConnection) { self.conn = conn }
    }

    private let queue = DispatchQueue(label: "relay", qos: .userInteractive)
    private var idle: [Tunnel] = []              // spare tunnels, oldest first
    private var waiting: [NWConnection] = []     // viewer connections waiting for a tunnel
    private var listeners: [NWListener] = []
    /// Called on the main thread when the laptop opens a tunnel (a cable PC is there).
    var onTunnel: (() -> Void)?

    /// True if a laptop is reachable over the cable right now. Call from the main thread.
    func hasTunnel() -> Bool { queue.sync { !idle.isEmpty } }

    func start() {
        queue.async { self.startListeners() }
    }

    /// Drop spare tunnels (e.g. after the app was in the background, when they
    /// are probably dead). The laptop notices and opens fresh ones.
    func reset() {
        queue.async {
            self.idle.forEach { self.close($0) }
            self.idle.removeAll()
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

    // MARK: - Tunnels

    private func gotTunnel(_ conn: NWConnection) {
        let t = Tunnel(conn)
        conn.stateUpdateHandler = { [weak self] state in
            switch state {
            case .failed, .cancelled: self?.close(t)
            default: break
            }
        }
        readTunnel(t)                       // watch it from the start
        if !waiting.isEmpty {
            pair(viewer: waiting.removeFirst(), tunnel: t)
        } else {
            idle.append(t)
            while idle.count > Relay.maxSpare { close(idle.removeFirst()) }
        }
        DispatchQueue.main.async { [weak self] in self?.onTunnel?() }
    }

    /// Laptop -> viewer. Runs for the tunnel's whole life, paired or not.
    private func readTunnel(_ t: Tunnel) {
        t.conn.receive(minimumIncompleteLength: 1, maximumLength: Relay.chunk) { [weak self] data, _, done, error in
            guard let self else { return }
            if let data, !data.isEmpty, let viewer = t.viewer {
                viewer.send(content: data, completion: .contentProcessed { err in
                    if err != nil { self.close(t) }
                    else if done || error != nil { self.close(t) }
                    else { self.readTunnel(t) }
                })
                return
            }
            if done || error != nil { self.close(t); return }
            self.readTunnel(t)   // (data before pairing never happens; ignore it)
        }
    }

    private func close(_ t: Tunnel) {
        guard !t.closed else { return }
        t.closed = true
        idle.removeAll { $0 === t }
        t.conn.cancel()
        t.viewer?.cancel()
    }

    // MARK: - Viewer

    private func gotViewer(_ conn: NWConnection) {
        if let t = idle.popLast() {          // newest spare tunnel: least likely to be stale
            pair(viewer: conn, tunnel: t)
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

    private func pair(viewer: NWConnection, tunnel t: Tunnel) {
        t.viewer = viewer
        viewer.stateUpdateHandler = { [weak self] state in
            switch state {
            case .failed, .cancelled: self?.close(t)
            default: break
            }
        }
        readViewer(t, viewer)
    }

    /// Viewer -> laptop.
    private func readViewer(_ t: Tunnel, _ viewer: NWConnection) {
        viewer.receive(minimumIncompleteLength: 1, maximumLength: Relay.chunk) { [weak self] data, _, done, error in
            guard let self else { return }
            if let data, !data.isEmpty {
                t.conn.send(content: data, completion: .contentProcessed { err in
                    if err != nil || done || error != nil { self.close(t) }
                    else { self.readViewer(t, viewer) }
                })
            } else if done || error != nil {
                self.close(t)
            } else {
                self.readViewer(t, viewer)
            }
        }
    }
}
