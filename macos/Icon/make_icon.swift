// Draws the app icon: a screen with a download arrow on a violet-to-teal tile.
// usage: swift make_icon.swift OUTPUT.png
import AppKit

let size = 1024.0
let canvas = NSRect(x: 0, y: 0, width: size, height: size)
let image = NSImage(size: canvas.size)
image.lockFocus()

// macOS icon grid: the tile sits inside a 100 pt margin with ~22% corner radius.
let tile = canvas.insetBy(dx: 100, dy: 100)
let tilePath = NSBezierPath(roundedRect: tile, xRadius: 185, yRadius: 185)
NSGraphicsContext.current?.saveGraphicsState()
let shadow = NSShadow()
shadow.shadowBlurRadius = 28
shadow.shadowOffset = NSSize(width: 0, height: -12)
shadow.shadowColor = NSColor.black.withAlphaComponent(0.35)
shadow.set()
NSColor.black.setFill()
tilePath.fill()
NSGraphicsContext.current?.restoreGraphicsState()

NSGradient(colors: [
    NSColor(red: 0.36, green: 0.22, blue: 0.93, alpha: 1),
    NSColor(red: 0.07, green: 0.68, blue: 0.80, alpha: 1),
])!.draw(in: tilePath, angle: -60)

// The screen.
let screen = NSRect(x: 252, y: 360, width: 520, height: 330)
let screenPath = NSBezierPath(roundedRect: screen, xRadius: 46, yRadius: 46)
screenPath.lineWidth = 34
NSColor.white.setStroke()
screenPath.stroke()

// The arrow, from inside the screen down onto a tray line.
NSColor.white.setFill()
let shaft = NSBezierPath(roundedRect: NSRect(x: 482, y: 300, width: 60, height: 250), xRadius: 30, yRadius: 30)
shaft.fill()
let head = NSBezierPath()
head.move(to: NSPoint(x: 512, y: 214))
head.line(to: NSPoint(x: 646, y: 356))
head.line(to: NSPoint(x: 378, y: 356))
head.close()
head.lineJoinStyle = .round
head.lineWidth = 24
NSColor.white.setStroke()
head.stroke()
head.fill()
let tray = NSBezierPath(roundedRect: NSRect(x: 322, y: 168, width: 380, height: 30), xRadius: 15, yRadius: 15)
tray.fill()

image.unlockFocus()

guard
    let tiff = image.tiffRepresentation,
    let bitmap = NSBitmapImageRep(data: tiff),
    let png = bitmap.representation(using: .png, properties: [:])
else { fatalError("could not render the icon") }
try png.write(to: URL(fileURLWithPath: CommandLine.arguments[1]))
