import CoreTransferable
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// Pick a swing clip, declare its context (club, handedness, camera
/// angle), and stream it to the server for analysis.
struct UploadView: View {
    @Environment(AppSession.self) private var session

    @State private var pickerItem: PhotosPickerItem?
    @State private var pickedVideo: PickedVideo?
    @State private var importing = false

    @State private var club = ""
    @State private var hand = "right"
    @State private var angle = "face-on"
    @State private var level = ""
    @State private var fast = false
    @State private var notify = false

    @State private var uploading = false
    @State private var progress: Double = 0
    @State private var errorMessage: String?
    @State private var completedSessionID: String?

    var body: some View {
        NavigationStack {
            Form {
                videoSection
                contextSection
                optionsSection
                submitSection
                tipsSection
            }
            .navigationTitle("Analyze a swing")
            .navigationDestination(item: $completedSessionID) { id in
                SessionDetailView(sessionID: id)
            }
            .task {
                await prefillFromProfile()
            }
        }
    }

    // MARK: Sections

    private var videoSection: some View {
        Section("Swing video") {
            PhotosPicker(selection: $pickerItem, matching: .videos, photoLibrary: .shared()) {
                if let pickedVideo {
                    Label(pickedVideo.filename, systemImage: "video.fill")
                        .lineLimit(1)
                } else if importing {
                    HStack {
                        ProgressView()
                        Text("Preparing video…")
                            .foregroundStyle(.secondary)
                    }
                } else {
                    Label("Choose a video", systemImage: "video.badge.plus")
                }
            }
            .onChange(of: pickerItem) { _, newItem in
                loadPickedVideo(newItem)
            }
            if let pickedVideo {
                Text(pickedVideo.sizeDescription)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var contextSection: some View {
        Section("Context") {
            Picker("Club", selection: $club) {
                Text("Choose…").tag("")
                ForEach(GolfOptions.clubs) { option in
                    Text(option.label).tag(option.key)
                }
            }
            Picker("Handedness", selection: $hand) {
                ForEach(GolfOptions.hands) { option in
                    Text(option.label).tag(option.key)
                }
            }
            Picker("Camera angle", selection: $angle) {
                ForEach(GolfOptions.angles) { option in
                    Text(option.label).tag(option.key)
                }
            }
            Picker("Experience framing", selection: $level) {
                ForEach(GolfOptions.levels) { option in
                    Text(option.label).tag(option.key)
                }
            }
        }
    }

    private var optionsSection: some View {
        Section {
            Toggle("Fast mode", isOn: $fast)
            Toggle("Email me when coaching is ready", isOn: $notify)
        } footer: {
            Text("Fast mode skips the motion-interpolated slow-motion clip — results arrive in a fraction of the time.")
        }
    }

    private var submitSection: some View {
        Section {
            if uploading {
                VStack(alignment: .leading, spacing: 8) {
                    ProgressView(value: progress)
                    Text(progress < 1 ? "Uploading… \(Int(progress * 100))%" : "Processing on the server…")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            } else {
                Button {
                    upload()
                } label: {
                    Text("Upload for analysis")
                        .font(.headline)
                        .frame(maxWidth: .infinity)
                }
                .disabled(pickedVideo == nil || club.isEmpty)
            }
            if let errorMessage {
                Text(errorMessage)
                    .font(.footnote)
                    .foregroundStyle(.red)
            }
        } footer: {
            if club.isEmpty {
                Text("Pick the club you were swinging — benchmarks and coaching depend on it.")
            }
        }
    }

    private var tipsSection: some View {
        Section("Filming tips") {
            Label("Prop the phone at hip height, then film face-on (chest pointing at the camera).", systemImage: "iphone.gen3")
            Label("Keep your whole body and the club in frame through the finish.", systemImage: "person.crop.rectangle")
            Label("Leave the sound on — ball strikes are found by audio first.", systemImage: "speaker.wave.2")
            Label("One clip can hold several swings; each gets its own breakdown.", systemImage: "square.stack.3d.down.right")
        }
        .font(.subheadline)
    }

    // MARK: Actions

    private func prefillFromProfile() async {
        guard let client = session.client else { return }
        guard let me = try? await client.me(), let profile = me.profile else { return }
        if club.isEmpty { club = profile.preferredClub }
        hand = profile.handedness
        angle = profile.cameraAngle
    }

    private func loadPickedVideo(_ item: PhotosPickerItem?) {
        pickedVideo = nil
        errorMessage = nil
        guard let item else { return }
        importing = true
        Task {
            defer { importing = false }
            do {
                if let video = try await item.loadTransferable(type: PickedVideo.self) {
                    pickedVideo = video
                } else {
                    errorMessage = "That item couldn't be read as a video."
                }
            } catch {
                errorMessage = "Couldn't load the video: \(error.localizedDescription)"
            }
        }
    }

    private func upload() {
        guard let client = session.client, let video = pickedVideo else { return }
        uploading = true
        progress = 0
        errorMessage = nil
        let context = UploadContext(
            club: club,
            hand: hand,
            angle: angle,
            level: level,
            fast: fast,
            notifyByEmail: notify
        )
        Task {
            defer { uploading = false }
            do {
                let uploader = VideoUploader()
                let response = try await uploader.upload(
                    videoFile: video.url,
                    filename: video.filename,
                    context: context,
                    baseURL: client.baseURL,
                    token: client.token,
                    progress: { fraction in
                        Task { @MainActor in
                            progress = fraction
                        }
                    }
                )
                video.cleanUp()
                pickedVideo = nil
                pickerItem = nil
                completedSessionID = response.id
            } catch {
                session.handle(error)
                errorMessage = (error as? LocalizedError)?.errorDescription
                    ?? error.localizedDescription
            }
        }
    }
}

/// A picked library video copied into our sandbox so the uploader can
/// stream it. The copy keeps its original extension — the server accepts
/// .mov/.mp4/.m4v/.avi/.mkv by suffix.
struct PickedVideo: Transferable {
    let url: URL
    let filename: String
    let bytes: Int64

    var sizeDescription: String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }

    func cleanUp() {
        try? FileManager.default.removeItem(at: url)
    }

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { video in
            SentTransferredFile(video.url)
        } importing: { received in
            let source = received.file
            let ext = source.pathExtension.isEmpty ? "mov" : source.pathExtension
            let destination = FileManager.default.temporaryDirectory
                .appending(path: "picked-\(UUID().uuidString).\(ext)")
            try FileManager.default.copyItem(at: source, to: destination)
            let attributes = try? FileManager.default.attributesOfItem(atPath: destination.path)
            let bytes = (attributes?[.size] as? NSNumber)?.int64Value ?? 0
            return PickedVideo(
                url: destination,
                filename: source.lastPathComponent,
                bytes: bytes
            )
        }
    }
}
