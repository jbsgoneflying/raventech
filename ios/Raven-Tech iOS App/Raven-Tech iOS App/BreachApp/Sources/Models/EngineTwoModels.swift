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
    var bandPct: Double?
    var weightingMode: String?
    var gammaFlipStrike: Double?
    var dealerGamma: DealerGammaData?
    var oiClusters: OIClustersData?
    var gexHeatmap: SPXGexHeatmap?
    var weeklyFriday: SPXLevelView?
    var nearestDaily: SPXLevelView?
    var volPressure: VolPressureData?
    var warnings: [String]?
    var notes: [String]?

    // Convenience accessors for put/call walls
    var putWallStrike: Double? { oiClusters?.putWall?.peakStrike }
    var callWallStrike: Double? { oiClusters?.callWall?.peakStrike }

    // Convenience accessors for addons (from weekly view)
    var hedgingPressure: HedgingPressure? { weeklyFriday?.addons?.hedgingPressure }
    var tailIgnition: TailIgnition? { weeklyFriday?.addons?.tailIgnition }
}

struct SPXLevelView: Decodable {
    var enabled: Bool?
    var symbolUsed: String?
    var expiry: String?
    var spot: Double?
    var dealerGamma: DealerGammaData?
    var oiClusters: OIClustersData?
    var gammaFlipStrike: Double?
    var addons: LevelAddons?
    var warnings: [String]?
    var notes: [String]?
}

struct LevelAddons: Decodable {
    var hedgingPressure: HedgingPressure?
    var tailIgnition: TailIgnition?
}

struct HedgingPressure: Decodable {
    var label: String?
    var bucket: String?
    var reasons: [String]?
}

struct TailIgnition: Decodable {
    var label: String?
    var bucket: String?
    var reasons: [String]?
}

struct VolPressureData: Decodable {
    var enabled: Bool?
    var label: String?
    var bucket: String?
    var putCallRatio: Double?
    var ivRank: Double?
    var reasons: [String]?
}

struct DealerGammaData: Decodable {
    var spot: Double?
    var expiry: String?
    var bandPct: Double?
    var weightingMode: String?
    var callsGex: Double?
    var putsGex: Double?
    var netGex: Double?
    var netGammaSign: String?
    var magnitudeRatio: Double?
    var magnitudeBucket: String?
    var callPutImbalance: Double?
    var topGammaStrikes: [TopGammaStrike]?
    var warnings: [String]?
}

struct TopGammaStrike: Decodable, Identifiable {
    var strike: Double?
    var side: String?
    var gex: Double?
    var gamma: Double?
    var weight: Double?
    var id: String { "\(strike ?? 0)-\(side ?? "")" }
}

struct OIClustersData: Decodable {
    var spot: Double?
    var expiry: String?
    var bandPct: Double?
    var weightingMode: String?
    var strikeStep: Double?
    var clusterSteps: Int?
    var callClusters: [OICluster]?
    var putClusters: [OICluster]?
    var callWall: OICluster?
    var putWall: OICluster?
    var warnings: [String]?
}

struct OICluster: Decodable, Identifiable {
    var side: String?
    var minStrike: Double?
    var maxStrike: Double?
    var centerStrike: Double?
    var totalOI: Double?
    var peakStrike: Double?
    var peakOI: Double?
    var nStrikes: Int?
    var id: String { "\(peakStrike ?? 0)-\(side ?? "")" }
}

struct SPXGexHeatmap: Decodable {
    var enabled: Bool?
    var spot: Double?
    var strikes: [Double]?
    var expiries: [String]?
    var matrix: [[Double?]]?
    var matrixSlope: [[Double?]]?
    var downsideAccelStart: Double?
    var upsideAccelStart: Double?
    var stability: SPXHeatStability?
    var warnings: [String]?
    var notes: [String]?
}

struct SPXHeatStability: Decodable {
    var label: String?
    var reasons: [String]?
    var downsideDistancePts: Double?
    var upsideDistancePts: Double?
    var downsideDistanceEm: Double?
    var upsideDistanceEm: Double?
}
