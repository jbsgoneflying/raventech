import Foundation

// MARK: - Engine 1 (Breach) API Response Models

struct BreachResponse: Decodable {
    var enabled: Bool?
    var schemaVersion: Int?
    var ticker: String?
    var asOfDate: String?
    var underlyingPrice: Double?
    var summary: BreachSummary?
    var quarters: [String: QuarterStats]?
    var events: [BreachEvent]
    var wingRecommendation: WingRecommendation?
    var regime: RegimeData?
    var goNoGo: GoNoGoDecision?

    enum CodingKeys: String, CodingKey {
        case enabled, schemaVersion, ticker, asOfDate, underlyingPrice
        case summary, quarters, events, wingRecommendation, regime, goNoGo
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        self.enabled = try c.decodeIfPresent(Bool.self, forKey: .enabled)
        self.schemaVersion = try c.decodeIfPresent(Int.self, forKey: .schemaVersion)
        self.ticker = try c.decodeIfPresent(String.self, forKey: .ticker)
        self.asOfDate = try c.decodeIfPresent(String.self, forKey: .asOfDate)
        self.underlyingPrice = try c.decodeIfPresent(Double.self, forKey: .underlyingPrice)
        self.summary = try c.decodeIfPresent(BreachSummary.self, forKey: .summary)
        self.quarters = try c.decodeIfPresent([String: QuarterStats].self, forKey: .quarters)
        self.events = (try? c.decode([BreachEvent].self, forKey: .events)) ?? []
        self.wingRecommendation = try c.decodeIfPresent(WingRecommendation.self, forKey: .wingRecommendation)
        self.regime = try c.decodeIfPresent(RegimeData.self, forKey: .regime)
        self.goNoGo = try c.decodeIfPresent(GoNoGoDecision.self, forKey: .goNoGo)
    }
}

struct BreachSummary: Decodable {
    var eventsFound: Int?
    var eventsUsed: Int?
    var breaches: Int?
    var breachRatePct: Double?
    var avgImpliedAllPct: Double?
    var avgRealizedAllPct: Double?
    var avgAboveBreachPct: Double?
    var avgRealizedIfBreachPct: Double?
    var avgUpOvershootPct: Double?
    var avgDownOvershootPct: Double?
    var upBreachRatePct: Double?
    var downBreachRatePct: Double?
    var upBreaches: Int?
    var downBreaches: Int?
    var tailBias: String?
}

/// Quarter stats from the backend (dictionary values)
struct QuarterStats: Decodable {
    var eventsTotal: Int?
    var eventsUsed: Int?
    var breaches: Int?
    var breachRatePct: Double?
    var avgRealizedAllPct: Double?
    var avgImpliedAllPct: Double?
    var quarterUpBreachRatePct: Double?
    var quarterDownBreachRatePct: Double?
    var recommendation: String?
}

struct BreachEvent: Decodable, Identifiable {
    var id: String { "\(earnDate ?? "")|\(timing ?? "")" }
    var earnDate: String?
    var timing: String?
    var anncTod: String?
    var impliedMovePct: Double?
    var realizedMovePct: Double?
    var breach: Bool?
    var moveDirection: String?
    var signedMovePct: Double?
    var closePx: Double?
    var openPx: Double?
    var upBreach: Bool?
    var downBreach: Bool?
    var breachSide: String?
}

struct WingRecommendation: Decodable {
    var mode: String?
    var callWingMultiple: Double?
    var putWingMultiple: Double?
    var callWidthEm: Double?
    var putWidthEm: Double?
    var recommendation: String?
    var recommendationLabel: String?
    var rationale: String?
    var tradeGate: String?
    var structureRationale: String?
    var notes: [String]?
}

struct RegimeData: Decodable {
    var score100: Double?
    var bucket: String?
    var components: RegimeComponents?
}

struct RegimeComponents: Decodable {
    var vix: Double?
    var vvix: Double?
    var skew: Double?
    var pcRatio: Double?
}

// MARK: - GO/NO-GO Decision

struct GoNoGoDecision: Decodable {
    var status: String?
    var passed: Bool?
    var checks: [GoNoGoCheck]?
    var warnings: [String]?
    var notes: [String]?
}

struct GoNoGoCheck: Decodable, Identifiable {
    var id: String { checkId ?? UUID().uuidString }
    var checkId: String?
    var label: String?
    var state: String?  // PASS, FAIL, WARN, MISSING
    var code: String?
    var explain: String?
    var threshold: GoNoGoThreshold?

    enum CodingKeys: String, CodingKey {
        case checkId = "id"
        case label, state, code, explain, threshold
    }
}

struct GoNoGoThreshold: Decodable {
    var min: Double?
    var max: Double?
    var minEvents: Int?
    var maxBreachPct: Double?
}
