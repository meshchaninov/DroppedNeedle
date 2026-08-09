import { api } from '$lib/api/client';
import { API } from '$lib/constants';
import { authStore } from '$lib/stores/authStore.svelte';
import type { SpotifyLikedSyncStatus } from '$lib/types';
import { createMutation, createQuery } from '@tanstack/svelte-query';
import {
	invalidateQueriesWithPersister,
	setQueryDataWithPersister
} from '$lib/queries/QueryClient';

const key = () => ['spotify-liked-sync', authStore.user?.id] as const;

export const getSpotifyLikedSyncQuery = () =>
	createQuery(() => ({
		queryKey: key(),
		queryFn: ({ signal }) =>
			api.global.get<SpotifyLikedSyncStatus>(API.me.spotifyLikedSync(), { signal }),
		refetchInterval: (query: { state: { data?: SpotifyLikedSyncStatus } }) =>
			query.state.data?.enabled ? 10_000 : false
	}));

export const createUpdateSpotifyLikedSyncMutation = () => {
	return createMutation(() => ({
		mutationFn: (settings: { enabled: boolean; include_existing: boolean }) =>
			api.global.put<SpotifyLikedSyncStatus>(API.me.spotifyLikedSync(), settings),
		onSuccess: (data) => setQueryDataWithPersister<SpotifyLikedSyncStatus>(key(), data)
	}));
};

export const createRunSpotifyLikedSyncMutation = () => {
	return createMutation(() => ({
		mutationFn: () =>
			api.global.post<{ status: 'started' | 'already_running' }>(API.me.spotifyLikedSyncRun()),
		onSuccess: async () => {
			await invalidateQueriesWithPersister({ queryKey: key() });
		}
	}));
};
