import SwiftUI
import WebKit

/// Renders the session's `report.html` — the same rich deliverable the web
/// product serves — inside the app.
///
/// The report and its media live behind bearer-token auth, and WKWebView
/// only attaches custom headers to the main document, not to the images
/// and clips the report references. So the page is loaded through a
/// custom `cireport://` scheme whose handler rewrites every request to the
/// real server, attaches the Authorization header, and streams the bytes
/// back — main document and subresources alike.
struct ReportView: View {
    @Environment(AppSession.self) private var session
    let reportPath: String

    var body: some View {
        Group {
            if let client = session.client {
                ReportWebView(
                    baseURL: client.baseURL,
                    token: client.token,
                    path: reportPath
                )
                .ignoresSafeArea(edges: .bottom)
            } else {
                ContentUnavailableView(
                    "Not connected",
                    systemImage: "wifi.slash",
                    description: Text("Reconnect this device to view reports.")
                )
            }
        }
        .navigationTitle("Report")
        .navigationBarTitleDisplayMode(.inline)
    }
}

private struct ReportWebView: UIViewRepresentable {
    let baseURL: URL
    let token: String
    let path: String

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.setURLSchemeHandler(
            AuthProxySchemeHandler(baseURL: baseURL, token: token),
            forURLScheme: AuthProxySchemeHandler.scheme
        )
        configuration.allowsInlineMediaPlayback = true
        configuration.mediaTypesRequiringUserActionForPlayback = []
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.isOpaque = false
        webView.backgroundColor = .systemBackground
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard webView.url == nil else { return }
        var components = URLComponents()
        components.scheme = AuthProxySchemeHandler.scheme
        components.host = baseURL.host
        components.path = path.hasPrefix("/") ? path : "/" + path
        if let url = components.url {
            webView.load(URLRequest(url: url))
        }
    }
}

/// Proxies `cireport://` requests to the paired server over HTTPS with the
/// device token attached, streaming response bytes through to WebKit.
final class AuthProxySchemeHandler: NSObject, WKURLSchemeHandler, URLSessionDataDelegate {
    static let scheme = "cireport"

    private let baseURL: URL
    private let token: String

    /// Keyed by URLSession task identifier. Only touched on the main
    /// queue — both WKURLSchemeHandler callbacks and this URLSession's
    /// delegate queue run there — so no extra locking is needed. Entries
    /// are removed on stop, which is also what keeps this handler from
    /// calling a WKURLSchemeTask after WebKit told us to stop (that's a
    /// hard crash).
    private var inflight: [Int: (data: URLSessionDataTask, scheme: WKURLSchemeTask)] = [:]

    private lazy var session = URLSession(
        configuration: .ephemeral,
        delegate: self,
        delegateQueue: .main
    )

    init(baseURL: URL, token: String) {
        self.baseURL = baseURL
        self.token = token
    }

    func webView(_ webView: WKWebView, start urlSchemeTask: WKURLSchemeTask) {
        guard let requested = urlSchemeTask.request.url,
              var components = URLComponents(url: requested, resolvingAgainstBaseURL: false),
              let base = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else {
            urlSchemeTask.didFailWithError(URLError(.badURL))
            return
        }
        components.scheme = base.scheme
        components.host = base.host
        components.port = base.port
        guard let url = components.url else {
            urlSchemeTask.didFailWithError(URLError(.badURL))
            return
        }

        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        // Forward the headers that matter for media playback — Range makes
        // <video> seeking work when the server honors byte ranges.
        for header in ["Range", "Accept", "Accept-Language", "If-None-Match", "If-Modified-Since"] {
            if let value = urlSchemeTask.request.value(forHTTPHeaderField: header) {
                request.setValue(value, forHTTPHeaderField: header)
            }
        }

        let dataTask = session.dataTask(with: request)
        inflight[dataTask.taskIdentifier] = (dataTask, urlSchemeTask)
        dataTask.resume()
    }

    func webView(_ webView: WKWebView, stop urlSchemeTask: WKURLSchemeTask) {
        for (identifier, entry) in inflight where entry.scheme === urlSchemeTask {
            inflight.removeValue(forKey: identifier)
            entry.data.cancel()
        }
    }

    // MARK: URLSessionDataDelegate (main queue)

    func urlSession(
        _ session: URLSession,
        dataTask: URLSessionDataTask,
        didReceive response: URLResponse,
        completionHandler: @escaping (URLSession.ResponseDisposition) -> Void
    ) {
        inflight[dataTask.taskIdentifier]?.scheme.didReceive(response)
        completionHandler(.allow)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        inflight[dataTask.taskIdentifier]?.scheme.didReceive(data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        guard let entry = inflight.removeValue(forKey: task.taskIdentifier) else { return }
        if let error {
            entry.scheme.didFailWithError(error)
        } else {
            entry.scheme.didFinish()
        }
    }
}
