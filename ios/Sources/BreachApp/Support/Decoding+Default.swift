import Foundation

// Helpers to allow optional/absent fields to fall back to defaults without throwing.
protocol DefaultValue {
    associatedtype Value: Decodable
    static var defaultValue: Value { get }
}

enum Defaults {
    enum EmptyString: DefaultValue { static let defaultValue = "" }
    enum EmptyArray<T: Decodable & ExpressibleByArrayLiteral>: DefaultValue { static var defaultValue: T { [] } }
    enum Zero: DefaultValue { static let defaultValue = 0 }
    enum ZeroDouble: DefaultValue { static let defaultValue = 0.0 }
    enum False: DefaultValue { static let defaultValue = false }
}

@propertyWrapper
struct Default<T: DefaultValue> {
    var wrappedValue: T.Value
}

extension Default: Decodable {
    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        wrappedValue = (try? container.decode(T.Value.self)) ?? T.defaultValue
    }
}
