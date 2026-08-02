// Render the WhisperN application icon as a PNG set for iconutil.
//
// Swift rather than a checked-in binary blob: the icon is then reviewable as
// code and reproducible from a clean checkout, and it needs no image library.
// Xcode Command Line Tools ship the `swift` interpreter, which the project
// already requires for whisper.cpp.
//
// Usage: swift scripts/make_icon.swift <output-iconset-dir>

import AppKit

let args = CommandLine.arguments
guard args.count == 2 else {
    FileHandle.standardError.write("usage: make_icon.swift <iconset-dir>\n".data(using: .utf8)!)
    exit(2)
}
let outDir = URL(fileURLWithPath: args[1])
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

// The application's own palette, from scripts/whispern_app.py: the panel dark
// and the record-button blue. The icon should read as the same object as the
// window it opens.
let top    = NSColor(srgbRed: 0.164, green: 0.192, blue: 0.251, alpha: 1)  // #2a3140
let bottom = NSColor(srgbRed: 0.086, green: 0.102, blue: 0.137, alpha: 1)  // #161a23
let accent = NSColor(srgbRed: 0.357, green: 0.545, blue: 0.820, alpha: 1)  // #5b8bd1

/// Draw the icon at exactly `px` by `px` pixels.
///
/// The bitmap rep is built explicitly rather than by locking focus on an
/// NSImage: lockFocus adopts the display's backing scale, which silently
/// produces 2x pixels on a Retina Mac and an iconset that iconutil rejects.
func render(_ px: Int) -> Data {
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: px, pixelsHigh: px,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    rep.size = NSSize(width: px, height: px)   // one point == one pixel

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)

    let s = CGFloat(px)
    // macOS icons sit in a rounded square inset from the tile edge, so the
    // grid stays even against system icons in the Dock.
    let inset = s * 0.085
    let body = NSRect(x: inset, y: inset, width: s - inset * 2, height: s - inset * 2)
    let squircle = NSBezierPath(roundedRect: body,
                                xRadius: s * 0.225, yRadius: s * 0.225)
    NSGradient(colors: [top, bottom])!.draw(in: squircle, angle: -90)

    // A hairline lip so the tile keeps an edge against a dark wallpaper.
    NSColor(white: 1, alpha: 0.09).setStroke()
    squircle.lineWidth = max(1, s * 0.006)
    squircle.stroke()

    // SF Symbol rather than hand-built paths: it stays legible at 16 px, where
    // a drawn microphone turns to mush.
    let cfg = NSImage.SymbolConfiguration(pointSize: s * 0.44, weight: .semibold)
    if let sym = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: nil)?
        .withSymbolConfiguration(cfg) {
        let tinted = NSImage(size: sym.size)
        tinted.lockFocus()
        sym.draw(at: .zero, from: .zero, operation: .sourceOver, fraction: 1)
        accent.set()
        NSRect(origin: .zero, size: sym.size).fill(using: .sourceAtop)
        tinted.unlockFocus()

        let p = NSPoint(x: (s - sym.size.width) / 2, y: (s - sym.size.height) / 2)
        tinted.draw(at: p, from: .zero, operation: .sourceOver, fraction: 1)
    }

    NSGraphicsContext.restoreGraphicsState()
    return rep.representation(using: .png, properties: [:])!
}

// The filenames iconutil requires. Each nominal size needs its 2x companion.
let wanted: [(String, Int)] = [
    ("icon_16x16.png", 16),    ("icon_16x16@2x.png", 32),
    ("icon_32x32.png", 32),    ("icon_32x32@2x.png", 64),
    ("icon_128x128.png", 128), ("icon_128x128@2x.png", 256),
    ("icon_256x256.png", 256), ("icon_256x256@2x.png", 512),
    ("icon_512x512.png", 512), ("icon_512x512@2x.png", 1024),
]

for (name, px) in wanted {
    try render(px).write(to: outDir.appendingPathComponent(name))
}
print("wrote \(wanted.count) PNGs to \(outDir.path)")
