interface MetricCardProps {
  label: string
  value: string | number
  valueColor?: string
  className?: string
}

export function MetricCard({ label, value, valueColor, className = '' }: MetricCardProps) {
  return (
    <div className={`bg-surface-container-high p-4 rounded border border-outline-variant ${className}`}>
      <div className="text-[11px] font-bold tracking-[0.05em] text-on-surface-variant mb-1 uppercase">
        {label}
      </div>
      <div className={`text-xl font-semibold tracking-tight ${valueColor || 'text-on-surface'}`}>
        {value}
      </div>
    </div>
  )
}
