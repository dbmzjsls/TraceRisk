import { computed, toValue, type MaybeRefOrGetter } from 'vue'
import { useQuery } from '@tanstack/vue-query'
import { apiRequest } from '../../api/client'
import { companiesSchema, companySchema } from '../../api/schemas'
export function useCompanies() { return useQuery({ queryKey: ['companies'], queryFn: ({ signal }) => apiRequest('/companies', companiesSchema, { signal }), retry: false }) }
export function useCompany(id: MaybeRefOrGetter<string>) { return useQuery({ queryKey: computed(() => ['companies', toValue(id)]), queryFn: ({ signal }) => apiRequest(`/companies/${encodeURIComponent(toValue(id))}`, companySchema, { signal }), retry: false }) }
