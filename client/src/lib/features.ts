import { useQuery } from "@tanstack/react-query"
import { api } from "./api"

export function useFeatures() {
  return useQuery({ queryKey: ["features"], queryFn: () => api.getFeatures(), staleTime: 60_000 })
}
