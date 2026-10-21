import type { Plan, Station } from './model.ts'

export type Position = [number, number]
export const truckColors = ['#4f46e5', '#0f766e', '#c2410c', '#be185d', '#0369a1', '#7e22ce']
export type MapPoint = {
  station: Station
  position: Position
  visits: { seq: number; action: 'PICKUP' | 'DROPOFF'; bikes: number }[]
  unserved: boolean
  excluded: boolean
}
export type VisitLine = {
  type: 'Feature'
  properties: { truckId: string; color: string; selected: boolean; fromSeq: number; toSeq: number }
  geometry: { type: 'LineString'; coordinates: Position[] }
}

/** MapLibre uses longitude, latitude. Reject missing/non-finite/out-of-map coordinates, including runtime nulls. */
export function position(station?: Pick<Station, 'lat' | 'lon'>): Position | null {
  if (!station || !Number.isFinite(station.lat) || !Number.isFinite(station.lon)) return null
  if (Math.abs(station.lat) > 85.051129 || Math.abs(station.lon) > 180) return null
  return [station.lon, station.lat]
}
export function truckColor(plan: Plan | null, truckId: string): string {
  const ids = [...new Set(plan?.routes.map(r => r.truckId) ?? [])].sort()
  return truckColors[Math.max(0, ids.indexOf(truckId)) % truckColors.length]!
}

export function mapData(stations: Station[], plan: Plan | null, selectedTruck: string, excluded: string[]) {
  const byId = new Map(stations.map(s => [s.stationId, s]))
  const unserved = new Set(plan?.unserved.map(s => s.stationId) ?? [])
  const exclude = new Set(excluded)
  const selectedStops = [...(plan?.routes.find(r => r.truckId === selectedTruck)?.stops ?? [])].sort((a, b) => a.seq - b.seq)
  const points: MapPoint[] = stations.flatMap(station => {
    const coordinate = position(station)
    return coordinate ? [{
      station, position: coordinate,
      visits: selectedStops.filter(s => s.stationId === station.stationId).map(s => ({ seq: s.seq, action: s.action, bikes: s.bikes })),
      unserved: unserved.has(station.stationId), excluded: exclude.has(station.stationId),
    }] : []
  })
  const features: VisitLine[] = []
  for (const route of plan?.routes ?? []) {
    const stops = [...route.stops].sort((a, b) => a.seq - b.seq)
    for (let i = 1; i < stops.length; i++) {
      const from = stops[i - 1]!, to = stops[i]!
      const a = position(byId.get(from.stationId)), b = position(byId.get(to.stationId))
      // Do not bridge over an unknown stop: that would imply a different visit order.
      if (a && b) features.push({
        type: 'Feature', properties: {
          truckId: route.truckId, color: truckColor(plan, route.truckId), selected: route.truckId === selectedTruck,
          fromSeq: from.seq, toSeq: to.seq,
        }, geometry: { type: 'LineString', coordinates: [a, b] },
      })
    }
  }
  const plannedIds = new Set([...(plan?.routes.flatMap(r => r.stops.map(s => s.stationId)) ?? []), ...unserved])
  const missingIds = [...plannedIds].filter(id => !position(byId.get(id)))
  return {
    points, lines: { type: 'FeatureCollection' as const, features }, missingIds,
    invalidCount: stations.length - points.length,
    selectedPositions: selectedStops.flatMap(s => { const p = position(byId.get(s.stationId)); return p ? [p] : [] }),
  }
}
