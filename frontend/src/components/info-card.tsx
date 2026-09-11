interface InfoCardProps {
  icon: string
  label: string
  value: string
  subtitle?: string
}

export function InfoCard({ icon, label, value, subtitle }: InfoCardProps) {
  return (
    <div className="bg-surface-container border border-outline-variant rounded p-4 flex flex-col justify-between hover:bg-surface-container-high transition-colors">
      <div className="flex items-center gap-2 text-on-surface-variant mb-1">
        <span className="material-symbols-outlined text-[16px]">{icon}</span>
        <span className="text-[11px] font-bold tracking-[0.05em] uppercase">{label}</span>
      </div>
      <div className="text-sm font-semibold text-on-surface">{value}</div>
      {subtitle && (
        <div className="text-xs text-on-surface-variant mt-1">{subtitle}</div>
      )}
    </div>
  )
}
