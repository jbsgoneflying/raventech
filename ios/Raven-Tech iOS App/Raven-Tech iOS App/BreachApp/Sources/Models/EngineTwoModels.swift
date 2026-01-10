import Foundation

// MARK: - Engine 2 (SPX IC) models (subset used by iOS UI)

struct SPXICResponse: Decodable {
    var enabled: Bool?
    var schemaVersion: Int?
    var asOfDate: String?
    var underlying: Engine2Underlying?
    var current: Engine2Current?
    var oddsLikeNow: Engine2OddsLikeNow?
    var notes: [String]?
}

struct Engine2Underlying: Decodable {
    var symbol: String?
    var isProxy: Bool?
    var proxyFor: String?
    var last: Double?
    var open: Double?
    var high: Double?
    var low: Double?
    var close: Double?
    var volume: Double?
}

struct Engine2Current: Decodable {
    var regime: Engine2Regime?
    var macro: Engine2Macro?
    var vwap: Engine2VWAP?
}

struct Engine2Regime: Decodable {
    var score100: Double?
    var bucket: String?

    // Convenience accessor
    var score: Double? { score100 }
}

struct Engine2Macro: Decodable {
    var multiplier: Double?
    var flags: [String: Bool]?
    var highImpactUS: Engine2HighImpactUS?

    // Convenience accessors
    var macroMultiplier: Double? { multiplier }
    var highImpactCount: Int? { highImpactUS?.count }
}

struct Engine2HighImpactUS: Decodable {
    var count: Int?
    var top: [String]?
}

struct Engine2VWAP: Decodable {
    var enabled: Bool?
    var value: Double?
    var livePrice: Double?
    var barDateUsed: String?
}

struct Engine2OddsLikeNow: Decodable {
    var weeksUsed: Int?
    var regimeBucket: String?
    var macroBucket: String?
    var seasonBucket: String?
    var byWidth: [Engine2OddsRow]?

    // Convenience accessors for specific widths
    var width10: Engine2OddsRow? { byWidth?.first { $0.w == 1.0 } }
    var width15: Engine2OddsRow? { byWidth?.first { $0.w == 1.5 } }
    var width20: Engine2OddsRow? { byWidth?.first { $0.w == 2.0 } }
}

struct Engine2OddsRow: Decodable, Identifiable {
    var w: Double?
    var n: Int?
    var breachEitherPct: Double?
    var breachPutPct: Double?
    var breachCallPct: Double?
    var avgAbsRetPct: Double?

    var id: String { String(format: "%.3f", w ?? 0) }
}

// MARK: - Engine 2 levels (subset used by iOS UI)

struct SPXLevelsResponse: Decodable {
    var schemaVersion: Int?
    var priceSeries: [SPXPricePoint]?
    var levels: SPXLiveLevels?
}

struct SPXPricePoint: Decodable, Identifiable {
    var date: String?
    var close: Double?
    var id: String { (date ?? UUID().uuidString) }
}

struct SPXLiveLevels: Decodable {
    var enabled: Bool?
    var view: String?
    var symbolUsed: String?
    var expiry: String?
    var spot: Double?
    var gammaFlipStrike: Double?
    var putWallStrike: Double?
    var callWallStrike: Double?
    var downsideAccelStart: Double?
    var upsideAccelStart: Double?
    var warnings: [String]?
    var notes: [String]?
    var gexHeatmap: SPXGexHeatmap?
    var stability: SPXStabilityInfo?
    var priceHistory: [SPXPricePoint]?
}

struct SPXStabilityInfo: Decodable {
    var weeklyStability: SPXHeatStability?
    var downsideDistancePts: Double?
    var upsideDistancePts: Double?
    var downsideDistanceEm: Double?
    var upsideDistanceEm: Double?
}

struct SPXGexHeatmap: Decodable {
    var enabled: Bool?
    var stability: SPXHeatStability?
    var metrics: SPXHeatMetrics?
    var warnings: [String]?
    var notes: [String]?
    var strikes: [Double]?
    var expiries: [String]?
    var matrix: [[Double?]]?
}

struct SPXHeatStability: Decodable {
    var label: String?
    var reasons: [String]?
}

struct SPXHeatMetrics: Decodable {
    var downsideDistancePts: Double?
    var upsideDistancePts: Double?
    var downsideDistanceEm: Double?
    var upsideDistanceEm: Double?
}
