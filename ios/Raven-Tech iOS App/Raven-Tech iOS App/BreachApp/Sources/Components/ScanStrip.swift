import SwiftUI

/// Summary scan strip showing earnings and macro counts
struct ScanStrip: View {
    let earnings: EarningsScan
    let macro: MacroScan

    struct EarningsScan {
        let total: Int
        let bmo: Int
        let amc: Int
        let unk: Int
    }

    struct MacroScan {
        let total: Int
        let fed: Int
        let econ: Int
    }

    var body: some View {
        HStack(spacing: 10) {
            scanCard(
                title: "Earnings",
                value: "\(earnings.total)",
                detail: "BMO \(earnings.bmo) · AMC \(earnings.amc) · UNK \(earnings.unk)"
            )

            scanCard(
                title: "Macro",
                value: "\(macro.total)",
                detail: "FED \(macro.fed) · ECON \(macro.econ)"
            )
        }
    }

    @ViewBuilder
    private func scanCard(title: String, value: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title)
                .font(.caption)
                .fontWeight(.semibold)
                .foregroundStyle(.secondary)

            Text(value)
                .font(.title2)
                .fontWeight(.bold)
                .monospacedDigit()

            Text(detail)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.ultraThinMaterial)
        .clipShape(RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .stroke(Color.black.opacity(0.08), lineWidth: 1)
        )
    }
}

/// A header for the calendar with navigation
struct CalendarHeader: View {
    let title: String
    let subtitle: String
    var onRefresh: (() -> Void)?
    var onPrevious: (() -> Void)?
    var onNext: (() -> Void)?

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title)
                    .font(.headline)
                    .fontWeight(.bold)

                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            Spacer()

            HStack(spacing: 8) {
                if let onPrevious = onPrevious {
                    Button(action: onPrevious) {
                        Image(systemName: "chevron.left")
                            .font(.caption.bold())
                            .frame(width: 34, height: 34)
                            .background(.ultraThinMaterial)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .overlay(
                                RoundedRectangle(cornerRadius: 10)
                                    .stroke(Color.black.opacity(0.08), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }

                if let onNext = onNext {
                    Button(action: onNext) {
                        Image(systemName: "chevron.right")
                            .font(.caption.bold())
                            .frame(width: 34, height: 34)
                            .background(.ultraThinMaterial)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .overlay(
                                RoundedRectangle(cornerRadius: 10)
                                    .stroke(Color.black.opacity(0.08), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }

                if let onRefresh = onRefresh {
                    Button(action: onRefresh) {
                        Image(systemName: "arrow.clockwise")
                            .font(.caption.bold())
                            .frame(width: 34, height: 34)
                            .background(.ultraThinMaterial)
                            .clipShape(RoundedRectangle(cornerRadius: 10))
                            .overlay(
                                RoundedRectangle(cornerRadius: 10)
                                    .stroke(Color.black.opacity(0.08), lineWidth: 1)
                            )
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .padding(.horizontal)
    }
}

#Preview {
    VStack(spacing: 16) {
        CalendarHeader(
            title: "January 2025",
            subtitle: "Jan 6 - Jan 10",
            onRefresh: {},
            onPrevious: {},
            onNext: {}
        )

        ScanStrip(
            earnings: ScanStrip.EarningsScan(total: 45, bmo: 20, amc: 18, unk: 7),
            macro: ScanStrip.MacroScan(total: 8, fed: 2, econ: 6)
        )
        .padding(.horizontal)
    }
    .background(Color.gray.opacity(0.1))
}
