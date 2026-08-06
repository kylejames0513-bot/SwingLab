import Foundation

struct UploadContext {
    var club: String
    var hand: String
    var angle: String
    var level: String
    var fast: Bool
    var notifyByEmail: Bool
}

/// Streams one swing video to `POST /upload` as multipart form data.
///
/// The multipart body is assembled on disk and uploaded with
/// `uploadTask(with:fromFile:)` so a several-hundred-megabyte clip never
/// has to fit in memory. The `Accept: application/json` header selects the
/// server's JSON response (`{"id": ..., "url": ...}`) instead of the
/// browser redirect.
final class VideoUploader: NSObject, @unchecked Sendable {
    private var progressHandler: (@Sendable (Double) -> Void)?
    private var continuation: CheckedContinuation<UploadResponse, Error>?
    private var received = Data()
    private var task: URLSessionUploadTask?

    private lazy var session: URLSession = {
        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = 120
        configuration.timeoutIntervalForResource = 60 * 60
        return URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }()

    func upload(
        videoFile: URL,
        filename: String,
        context: UploadContext,
        baseURL: URL,
        token: String,
        progress: @escaping @Sendable (Double) -> Void
    ) async throws -> UploadResponse {
        let boundary = "caddieinsight-\(UUID().uuidString)"
        let bodyFile = try Self.writeMultipartBody(
            videoFile: videoFile,
            filename: filename,
            context: context,
            boundary: boundary
        )
        defer { try? FileManager.default.removeItem(at: bodyFile) }

        var request = URLRequest(url: baseURL.appending(path: "/upload"))
        request.httpMethod = "POST"
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(
            "multipart/form-data; boundary=\(boundary)",
            forHTTPHeaderField: "Content-Type"
        )

        progressHandler = progress
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                self.continuation = continuation
                let task = session.uploadTask(with: request, fromFile: bodyFile)
                self.task = task
                task.resume()
            }
        } onCancel: {
            task?.cancel()
        }
    }

    // MARK: Body assembly

    private static func writeMultipartBody(
        videoFile: URL,
        filename: String,
        context: UploadContext,
        boundary: String
    ) throws -> URL {
        let destination = FileManager.default.temporaryDirectory
            .appending(path: "upload-\(UUID().uuidString).body")
        FileManager.default.createFile(atPath: destination.path, contents: nil)
        let handle = try FileHandle(forWritingTo: destination)
        defer { try? handle.close() }

        func writeField(_ name: String, _ value: String) throws {
            let part = "--\(boundary)\r\n"
                + "Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n"
                + "\(value)\r\n"
            try handle.write(contentsOf: Data(part.utf8))
        }

        try writeField("club", context.club)
        try writeField("hand", context.hand)
        try writeField("angle", context.angle)
        if !context.level.isEmpty {
            try writeField("level", context.level)
        }
        if context.fast {
            try writeField("fast", "on")
        }
        if context.notifyByEmail {
            try writeField("notify", "on")
        }

        let safeName = filename.replacingOccurrences(of: "\"", with: "_")
        let fileHeader = "--\(boundary)\r\n"
            + "Content-Disposition: form-data; name=\"video\"; filename=\"\(safeName)\"\r\n"
            + "Content-Type: \(contentType(for: filename))\r\n\r\n"
        try handle.write(contentsOf: Data(fileHeader.utf8))

        let source = try FileHandle(forReadingFrom: videoFile)
        defer { try? source.close() }
        while let chunk = try source.read(upToCount: 1 << 20), !chunk.isEmpty {
            try handle.write(contentsOf: chunk)
        }

        try handle.write(contentsOf: Data("\r\n--\(boundary)--\r\n".utf8))
        return destination
    }

    private static func contentType(for filename: String) -> String {
        switch (filename as NSString).pathExtension.lowercased() {
        case "mov": "video/quicktime"
        case "m4v": "video/x-m4v"
        case "avi": "video/x-msvideo"
        case "mkv": "video/x-matroska"
        default: "video/mp4"
        }
    }
}

extension VideoUploader: URLSessionDataDelegate {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didSendBodyData bytesSent: Int64,
        totalBytesSent: Int64,
        totalBytesExpectedToSend: Int64
    ) {
        guard totalBytesExpectedToSend > 0 else { return }
        progressHandler?(Double(totalBytesSent) / Double(totalBytesExpectedToSend))
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        received.append(data)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        // One uploader serves one upload; releasing the session here also
        // breaks the session→delegate retain cycle.
        session.finishTasksAndInvalidate()
        guard let continuation else { return }
        self.continuation = nil
        if let error {
            continuation.resume(throwing: APIError.transport(error))
            return
        }
        guard let http = task.response as? HTTPURLResponse else {
            continuation.resume(throwing: APIError.transport(URLError(.badServerResponse)))
            return
        }
        switch http.statusCode {
        case 200...299:
            do {
                let decoder = JSONDecoder()
                decoder.keyDecodingStrategy = .convertFromSnakeCase
                continuation.resume(
                    returning: try decoder.decode(UploadResponse.self, from: received)
                )
            } catch {
                continuation.resume(throwing: APIError.decoding(error))
            }
        case 401:
            continuation.resume(throwing: APIError.unauthorized)
        default:
            continuation.resume(
                throwing: APIError.server(
                    status: http.statusCode,
                    message: APIClient.serverMessage(from: received, status: http.statusCode)
                )
            )
        }
    }
}
