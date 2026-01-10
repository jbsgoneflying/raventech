import SwiftUI

/// A point in a price series chart
struct PricePoint: Identifiable {
    let id = UUID()
    let date: String
    let close: Double

    init(date: String, close: Double) {
        self.date = date
        self.close = close
    }
}

/// An overlay level for price charts (walls, gamma flip, clusters)
struct ChartLevel: Identifiable {
    let id = UUID()
    let kind: LevelKind
    let value: Double
    let label: String
    var detail: String?

    enum LevelKind: String {
        case putWall
        case callWall
        case gammaFlip
        case cluster
        case gammaPeak

        var color: Color {
            switch self {
            case .putWall: return Color(hex: "DC2626").opacity(0.95)
            case .callWall: return Color(hex: "16A34A").opacity(0.95)
            case .gammaFlip: return Color(hex: "111827").opacity(0.72)
            case .cluster: return Color.black.opacity(0.44)
            case .gammaPeak: return Color(hex: "6366F1").opacity(0.92)
            }
        }

        var dashPattern: [CGFloat] {
            switch self {
            case .putWall, .callWall: return [6, 6]
            case .gammaFlip: return [10, 6]
            case .cluster: return [3, 6]
            case .gammaPeak: return [2, 7]
            }
        }
    }
}

/// A cell in a heatmap grid
struct HeatmapCell: Identifiable {
    let id = UUID()
    let row: Int
    let col: Int
    let value: Double?
}

/// Heatmap boundaries for acceleration zones
struct HeatmapBoundaries {
    let downsideStrike: Double?
    let upsideStrike: Double?
}

/// Heatmap stability assessment
struct HeatmapStability {
    let label: String
    let reasons: [String]

    var style: StabilityStyle {
        switch label.lowercased() {
        case "stable": return .stable
        case "asymmetric": return .asymmetric
        case "fragile": return .fragile
        default: return .unknown
        }
    }

    enum StabilityStyle {
        case stable
        case asymmetric
        case fragile
        case unknown

        var color: Color {
            switch self {
            case .stable: return Color(hex: "16A34A")
            case .asymmetric: return Color(hex: "EAB308")
            case .fragile: return Color(hex: "DC2626")
            case .unknown: return .secondary
            }
        }

        var backgroundColor: Color {
            switch self {
            case .stable: return Color(hex: "16A34A").opacity(0.10)
            case .asymmetric: return Color(hex: "EAB308").opacity(0.12)
            case .fragile: return Color(hex: "DC2626").opacity(0.10)
            case .unknown: return .secondary.opacity(0.10)
            }
        }

        var borderColor: Color {
            switch self {
            case .stable: return Color(hex: "16A34A").opacity(0.18)
            case .asymmetric: return Color(hex: "EAB308").opacity(0.20)
            case .fragile: return Color(hex: "DC2626").opacity(0.18)
            case .unknown: return .secondary.opacity(0.18)
            }
        }
    }
}

/// Distance metrics for heatmap display
struct HeatmapMetrics {
    let downsideDistancePts: Double?
    let upsideDistancePts: Double?
    let downsideDistanceEm: Double?
    let upsideDistanceEm: Double?
}

/// Configuration for price line chart appearance
struct PriceChartConfig {
    var lineColor: Color = Color(red: 15/255, green: 23/255, blue: 42/255, opacity: 0.86)
    var lineWidth: CGFloat = 2.2
    var showCrosshair: Bool = true
    var showOverlays: Bool = true
    var padding: EdgeInsets = EdgeInsets(top: 10, leading: 10, bottom: 10, trailing: 10)
}

/// Configuration for heatmap appearance
struct HeatmapConfig {
    var cellCornerRadius: CGFloat = 2
    var positiveColor: Color = Color(hex: "F97316") // Orange
    var negativeColor: Color = Color(hex: "3B82F6") // Blue
    var missingColor: Color = Color(white: 0.5, opacity: 0.10)
    var showSpotLine: Bool = true
    var showBoundaries: Bool = true
}

/// Helper to compute chart scaling
struct ChartScaling {
    let minX: Double
    let maxX: Double
    let minY: Double
    let maxY: Double
    let width: CGFloat
    let height: CGFloat
    let padding: EdgeInsets

    var plotWidth: CGFloat { width - padding.leading - padding.trailing }
    var plotHeight: CGFloat { height - padding.top - padding.bottom }

    func xPosition(for index: Int, count: Int) -> CGFloat {
        guard count > 1 else { return padding.leading + plotWidth / 2 }
        let fraction = CGFloat(index) / CGFloat(count - 1)
        return padding.leading + fraction * plotWidth
    }

    func yPosition(for value: Double) -> CGFloat {
        guard maxY > minY else { return padding.top + plotHeight / 2 }
        let fraction = (value - minY) / (maxY - minY)
        return padding.top + plotHeight * (1 - CGFloat(fraction))
    }

    func valueAtY(_ y: CGFloat) -> Double {
        let fraction = 1 - (y - padding.top) / plotHeight
        return minY + Double(fraction) * (maxY - minY)
    }

    func indexAtX(_ x: CGFloat, count: Int) -> Int {
        guard count > 1 else { return 0 }
        let fraction = (x - padding.leading) / plotWidth
        return max(0, min(count - 1, Int(round(fraction * CGFloat(count - 1)))))
    }
}
