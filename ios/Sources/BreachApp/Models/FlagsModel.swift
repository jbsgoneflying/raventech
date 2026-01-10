import Foundation

struct FlagsResponse: Decodable {
    let enableBenzinga: Bool?
    let benzingaEnableEventRisk: Bool?
    let enableEngine2SpxIc: Bool?
    let engine2DefaultYears: Int?
    let engine2DefaultEmMults: String?
    let engine2DefaultWingPts: String?
    let engine2MacroMultiplierCap: Double?
    let engine2RequireOratsDailyVwap: Bool?
}
