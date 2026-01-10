import Foundation

struct BreachResponse: Decodable {
    var ticker: String?
    var params: BreachParams?
    var summary: BreachSummary?
    var baseline: BreachBaseline?
    var wingRecommendation: WingRecommendation?
    var regime: RegimeBlock?
    var quarters: [String: QuarterStats]?
    @Default<Defaults.EmptyArray<[BreachEvent]>> var events: [BreachEvent]
}

struct BreachParams: Decodable {
    var k: Double?
    var n: Int?
    var years: Int?
}

struct BreachSummary: Decodable {
    var breachRatePct: Double?
    var avgUpOvershootPct: Double?
    var avgDownOvershootPct: Double?
    var avgRealizedAllPct: Double?
    var avgImpliedAllPct: Double?
    var eventsUsed: Int?
}

struct BreachBaseline: Decodable {
    var breachRatePct: Double?
    var avgAboveBreachPct: Double?
    var avgRatioRealizedToImplied: Double?
    var eventsUsed: Int?
}

struct WingRecommendation: Decodable {
    var recommendationLabel: String?
    var rationale: String?
    var tailMultiplier: Double?
    var tas: Double?
    var callWingMultiple: Double?
    var putWingMultiple: Double?
    var structureMode: String?
    var tradeGate: String?
    var confidence: String?
}

struct RegimeBlock: Decodable {
    var label: String?
    var tailMultiplier: Double?
    var guidance: RegimeGuidance?
    var inputs: RegimeInputs?
}

struct RegimeGuidance: Decodable {
    var message: String?
    var tradeGate: String?
}

struct RegimeInputs: Decodable {
    var spyAbsRet5d: Double?
    var spyRv20: Double?
    var tickerIv30: Double?
}

struct QuarterStats: Decodable, Identifiable {
    var id: String { UUID().uuidString }
    var recommendation: String?
    var breachRatePct: Double?
    var avgRatioRealizedToImplied: Double?
    var maxRatioRealizedToImplied: Double?
    var quarterUpBreachRatePct: Double?
    var quarterDownBreachRatePct: Double?
    var nearBreachRatePct: [String: Double]?
}

struct BreachEvent: Decodable, Identifiable {
    var id: String { earnDate ?? UUID().uuidString }
    var earnDate: String?
    var pricingDateUsed: String?
    var openDateUsed: String?
    var closeDateUsed: String?
    var impliedMovePct: Double?
    var realizedMovePct: Double?
    var signedMovePct: Double?
    var breach: Bool?
    var breachSide: String?
    var timing: String?
    var anncTod: String?
    var moveDirection: String?
    var regimeAtEvent: RegimeBlock?
}
