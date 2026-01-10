import Foundation

struct CalendarResponse: Decodable {
    var view: String?
    var range: CalendarRange?
    var meta: CalendarMeta?
    @Default<Defaults.EmptyArray<[CalendarDay]>> var days: [CalendarDay]
}

struct CalendarRange: Decodable {
    var start: String?
    var end: String?
}

struct CalendarMeta: Decodable {
    var generatedAt: String?
    var engine1Only: Bool?
    var counts: CalendarCounts?
    var notes: [String]?
}

struct CalendarCounts: Decodable {
    var earningsRowsFetched: Int?
    var earningsRowsInRange: Int?
    var tickersSeen: Int?
    var tickersEligible: Int?
    var earningsSource: String?
    var earningsSnapshotKind: String?
    var snapshotEtDate: String?
    var universeMode: String?
    var universeSize: Int?
}

struct CalendarDay: Decodable, Identifiable {
    var id: String { date ?? UUID().uuidString }
    var date: String?
    var earnings: EarningsGroups?
    @Default<Defaults.EmptyArray<[CalendarEvent]>> var events: [CalendarEvent]
}

struct EarningsGroups: Decodable {
    @Default<Defaults.EmptyArray<[EarningsTicker]>> var bmo: [EarningsTicker]
    @Default<Defaults.EmptyArray<[EarningsTicker]>> var amc: [EarningsTicker]
    @Default<Defaults.EmptyArray<[EarningsTicker]>> var unk: [EarningsTicker]

    enum CodingKeys: String, CodingKey {
        case bmo = "BMO"
        case amc = "AMC"
        case unk = "UNK"
    }
}

struct EarningsTicker: Decodable, Identifiable {
    var id: String { ticker.uppercased() }
    var ticker: String

    init(ticker: String) {
        self.ticker = ticker
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        self.ticker = (try? container.decode(String.self)) ?? ""
    }
}

struct CalendarEvent: Decodable, Identifiable {
    var id: String { (key ?? title ?? short ?? UUID().uuidString) + (date ?? "") }
    var key: String?
    var kind: String?
    var title: String?
    var short: String?
    var date: String?
    var timeEt: String?
    var importance: Int?
    var playbook: Playbook?
    var forecast: Double?
    var previous: Double?
    var actual: Double?
    var unit: String?
}

struct Playbook: Decodable {
    var deskView: [String]?
    var watch: [String]?
}
