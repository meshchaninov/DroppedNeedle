import { api } from '$lib/api/client';
import { API } from '$lib/constants';
import { authStore } from '$lib/stores/authStore.svelte';
import type { YandexMusicLikedSyncStatus } from '$lib/types';
import { createMutation, createQuery } from '@tanstack/svelte-query';
import {
	invalidateQueriesWithPersister,
	setQueryDataWithPersister
} from '$lib/queries/QueryClient';

const key = () => ['yandex-music-liked-sync', authStore.user?.id] as const;

export const getYandexMusicLikedSyncQuery = () =>
	createQuery(() => ({
		queryKey: key(),
		queryFn: ({ signal }) =>
			api.global.get<YandexMusicLikedSyncStatus>(API.me.yandexMusicLikedSync(), { signal }),
		refetchInterval: (query: { state: { data?: YandexMusicLikedSyncStatus } }) =>
			query.state.data?.enabled ? 10_000 : false
	}));

export const createUpdateYandexMusicLikedSyncMutation = () =>
	createMutation(() => ({
		mutationFn: (settings: { enabled: boolean; include_existing: boolean }) =>
			api.global.put<YandexMusicLikedSyncStatus>(API.me.yandexMusicLikedSync(), settings),
		onSuccess: (data) => setQueryDataWithPersister<YandexMusicLikedSyncStatus>(key(), data)
	}));

export const createRunYandexMusicLikedSyncMutation = () =>
	createMutation(() => ({
		mutationFn: () =>
			api.global.post<{ status: 'started' | 'already_running' }>(API.me.yandexMusicLikedSyncRun()),
		onSuccess: () => invalidateQueriesWithPersister({ queryKey: key() })
	}));
