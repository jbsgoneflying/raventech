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
    var netGex: Double?
    var gamma: Double?
    var bpFromFlip: Double?
    var bandPct: Double?
    var nStrikes: Int?
    var reasons: [String]?
}

struct TailIgnition: Decodable {
    var label: String?
    var bucket: String?
    var downScore: Double?
    var downBucket: String?
    var upScore: Double?
    var upBucket: String?
    var putWallDistancePct: Double?
    var callWallDistancePct: Double?
    var flipDistancePct: Double?
    var reasons: [String]?
}

struct VolPressureData: Decodable {
    var enabled: Bool?
    var label: String?
    var bucket: String?
    var zScore: Double?
    var iv7: Double?
    var rv10: Double?
    var termStructure: String?
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
    var error: String?
    var spot: Double?
    var bandPct: Double?
    var atmIvUsedPct: Double?
    var scaleDenom: Double?
    var raw: HeatmapRawData?
    var composite: HeatmapCompositeData?
    var boundaries: HeatmapBoundariesData?
    var metrics: HeatmapMetricsData?
    var stability: SPXHeatStability?
    var warnings: [String]?
    var notes: [String]?

    // Convenience accessors for heatmap rendering
    var strikes: [Double]? { composite?.strikes ?? raw?.strikes }
    var expiries: [String]? { raw?.expiries }
    var downsideAccelStart: Double? { boundaries?.downsideAccelerationBoundaryStrike }
    var upsideAccelStart: Double? { boundaries?.upsideAccelerationBoundaryStrike }

    enum CodingKeys: String, CodingKey {
        case enabled, error, spot, bandPct, atmIvUsedPct, scaleDenom
        case raw, composite, boundaries, metrics, stability
        case warnings, notes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.enabled = try? c.decodeIfPresent(Bool.self, forKey: .enabled)
        self.error = try? c.decodeIfPresent(String.self, forKey: .error)
        self.spot = try? c.decodeIfPresent(Double.self, forKey: .spot)
        self.bandPct = try? c.decodeIfPresent(Double.self, forKey: .bandPct)
        self.atmIvUsedPct = try? c.decodeIfPresent(Double.self, forKey: .atmIvUsedPct)
        self.scaleDenom = try? c.decodeIfPresent(Double.self, forKey: .scaleDenom)
        self.raw = try? c.decodeIfPresent(HeatmapRawData.self, forKey: .raw)
        self.composite = try? c.decodeIfPresent(HeatmapCompositeData.self, forKey: .composite)
        self.boundaries = try? c.decodeIfPresent(HeatmapBoundariesData.self, forKey: .boundaries)
        self.metrics = try? c.decodeIfPresent(HeatmapMetricsData.self, forKey: .metrics)
        self.stability = try? c.decodeIfPresent(SPXHeatStability.self, forKey: .stability)
        self.warnings = try? c.decodeIfPresent([String].self, forKey: .warnings)
        self.notes = try? c.decodeIfPresent([String].self, forKey: .notes)
    }
}

struct HeatmapRawData: Decodable {
    var expiries: [String]?
    var strikes: [Double]?
    var netDollarGex: [[Double?]]?
    var slopeNetDollarGex: [[Double?]]?

    enum CodingKeys: String, CodingKey {
        case expiries, strikes, netDollarGex, slopeNetDollarGex
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.expiries = try? c.decodeIfPresent([String].self, forKey: .expiries)
        self.strikes = try? c.decodeIfPresent([Double].self, forKey: .strikes)
        self.netDollarGex = try? c.decodeIfPresent([[Double?]].self, forKey: .netDollarGex)
        self.slopeNetDollarGex = try? c.decodeIfPresent([[Double?]].self, forKey: .slopeNetDollarGex)
    }
}

struct HeatmapCompositeData: Decodable {
    var halfLifeDte: Double?
    var strikes: [Double]?
    var expiriesUsed: [String]?
    var buckets: [HeatmapBucket]?

    enum CodingKeys: String, CodingKey {
        case halfLifeDte, strikes, expiriesUsed, buckets
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.halfLifeDte = try? c.decodeIfPresent(Double.self, forKey: .halfLifeDte)
        self.strikes = try? c.decodeIfPresent([Double].self, forKey: .strikes)
        self.expiriesUsed = try? c.decodeIfPresent([String].self, forKey: .expiriesUsed)
        self.buckets = try? c.decodeIfPresent([HeatmapBucket].self, forKey: .buckets)
    }
}

struct HeatmapBucket: Decodable {
    var key: String?
    var label: String?
    var effectiveDte: Double?
    var expectedMovePts: Double?
    var netDollarGex: [Double?]?
    var slopeNetDollarGex: [Double?]?

    enum CodingKeys: String, CodingKey {
        case key, label, effectiveDte, expectedMovePts, netDollarGex, slopeNetDollarGex
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.key = try? c.decodeIfPresent(String.self, forKey: .key)
        self.label = try? c.decodeIfPresent(String.self, forKey: .label)
        self.effectiveDte = try? c.decodeIfPresent(Double.self, forKey: .effectiveDte)
        self.expectedMovePts = try? c.decodeIfPresent(Double.self, forKey: .expectedMovePts)
        self.netDollarGex = try? c.decodeIfPresent([Double?].self, forKey: .netDollarGex)
        self.slopeNetDollarGex = try? c.decodeIfPresent([Double?].self, forKey: .slopeNetDollarGex)
    }
}

struct HeatmapBoundariesData: Decodable {
    var flipAdjacentN: Int?
    var downsideAccelerationBoundaryStrike: Double?
    var upsideAccelerationBoundaryStrike: Double?
}

struct HeatmapMetricsData: Decodable {
    var downsideDistancePts: Double?
    var upsideDistancePts: Double?
    var downsideDistanceEm: Double?
    var upsideDistanceEm: Double?
}

struct SPXHeatStability: Decodable {
    var label: String?
    var reasons: [String]?
}
