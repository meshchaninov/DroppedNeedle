<script lang="ts">
	import { Loader2, RefreshCw } from 'lucide-svelte';
	import YandexMusicIcon from '$lib/components/YandexMusicIcon.svelte';
	import { getConnectionsQuery } from '$lib/queries/connections/ConnectionsQuery.svelte';
	import {
		createConnectYandexMusicMutation,
		createDisconnectMutation
	} from '$lib/queries/connections/ConnectionsMutations.svelte';
	import {
		createRunYandexMusicLikedSyncMutation,
		createUpdateYandexMusicLikedSyncMutation,
		getYandexMusicLikedSyncQuery
	} from '$lib/queries/yandex-music/YandexMusicLikedSyncQueries.svelte';

	const connectionsQuery = getConnectionsQuery();
	const connection = $derived(
		connectionsQuery.data?.connections.find((item) => item.service === 'yandex_music') ?? null
	);
	const connectMutation = createConnectYandexMusicMutation();
	const disconnectMutation = createDisconnectMutation();
	const likedSyncQuery = getYandexMusicLikedSyncQuery();
	const updateLikedSyncMutation = createUpdateYandexMusicLikedSyncMutation();
	const runLikedSyncMutation = createRunYandexMusicLikedSyncMutation();

	let token = $state('');
	let error = $state<string | null>(null);

	async function connect() {
		error = null;
		try {
			await connectMutation.mutateAsync(token.trim());
			token = '';
		} catch {
			error = 'Could not connect Yandex Music. Check the token and try again.';
		}
	}

	async function disconnect() {
		error = null;
		await disconnectMutation.mutateAsync('yandex_music');
	}

	async function updateLikedSync(enabled: boolean, includeExisting?: boolean) {
		error = null;
		try {
			await updateLikedSyncMutation.mutateAsync({
				enabled,
				include_existing: includeExisting ?? likedSyncQuery.data?.include_existing ?? false
			});
		} catch {
			error = 'Could not update Yandex Music liked-track sync.';
		}
	}

	async function runLikedSync() {
		error = null;
		try {
			await runLikedSyncMutation.mutateAsync();
		} catch {
			error = 'Could not start Yandex Music liked-track sync.';
		}
	}
</script>

<section>
	<h2
		class="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-widest text-base-content/50"
	>
		<YandexMusicIcon class="h-4 w-4 text-red-500" />
		Yandex Music
	</h2>

	<div
		class="space-y-3 rounded-2xl border border-base-300/50 bg-base-200/40 p-4 backdrop-blur-sm sm:p-5"
	>
		<div
			class="crate-card flex items-center justify-between gap-3 rounded-xl border border-base-300/40 bg-base-300/20 p-3"
		>
			<div class="flex min-w-0 items-center gap-3">
				<div
					class="flex h-10 w-10 items-center justify-center rounded-xl bg-red-500/10 text-red-500 ring-1 ring-red-500/20"
				>
					<YandexMusicIcon class="h-[1.15rem] w-[1.15rem]" />
				</div>
				<div class="min-w-0">
					<div class="flex items-center gap-2">
						<span class="text-sm font-semibold">Yandex Music</span>
						<span class="status {connection ? 'status-success' : 'status-error'} status-sm"></span>
					</div>
					{#if connection}
						<p class="truncate text-xs text-base-content/50">@{connection.username || 'linked'}</p>
					{:else}
						<p class="text-xs text-base-content/30">Not connected</p>
					{/if}
				</div>
			</div>

			{#if connection}
				<button
					type="button"
					class="btn btn-ghost btn-xs rounded-full"
					onclick={disconnect}
					disabled={disconnectMutation.isPending}
				>
					{#if disconnectMutation.isPending}<Loader2 class="h-3.5 w-3.5 animate-spin" />{/if}
					Disconnect
				</button>
			{/if}
		</div>

		{#if !connection}
			<form class="space-y-2" onsubmit={(event) => (event.preventDefault(), connect())}>
				<label class="form-control w-full">
					<span class="mb-1 text-xs text-base-content/60">OAuth token</span>
					<input
						type="password"
						class="input input-bordered input-sm w-full"
						placeholder="Paste your Yandex Music token"
						autocomplete="off"
						bind:value={token}
					/>
				</label>
				<div class="flex items-center justify-between gap-3">
					<a
						class="link text-xs text-base-content/50"
						href="https://github.com/MarshalX/yandex-music-token"
						target="_blank"
						rel="noreferrer">How to get a token</a
					>
					<button
						type="submit"
						class="btn btn-error btn-xs text-white"
						disabled={!token.trim() || connectMutation.isPending}
					>
						{#if connectMutation.isPending}<Loader2 class="h-3.5 w-3.5 animate-spin" />{/if}
						Connect
					</button>
				</div>
				<p class="text-xs text-base-content/35">
					The token is verified first and stored in DroppedNeedle's encrypted connection store.
				</p>
			</form>
		{:else}
			<div class="space-y-3 rounded-xl border border-base-300/40 bg-base-300/10 p-3">
				<div class="flex items-start justify-between gap-4">
					<div>
						<p class="text-sm font-medium">Automatically request liked tracks</p>
						<p class="mt-0.5 text-xs text-base-content/45">
							New Yandex Music likes are matched with MusicBrainz and sent to your configured
							download client.
						</p>
					</div>
					<input
						type="checkbox"
						class="toggle toggle-sm toggle-error mt-0.5"
						checked={likedSyncQuery.data?.enabled ?? false}
						disabled={likedSyncQuery.isPending || updateLikedSyncMutation.isPending}
						onchange={(event) => updateLikedSync(event.currentTarget.checked)}
					/>
				</div>

				{#if !likedSyncQuery.data?.initialized}
					<label class="flex cursor-pointer items-center gap-2 text-xs text-base-content/60">
						<input
							type="checkbox"
							class="checkbox checkbox-xs"
							checked={likedSyncQuery.data?.include_existing ?? false}
							disabled={updateLikedSyncMutation.isPending}
							onchange={(event) =>
								updateLikedSync(likedSyncQuery.data?.enabled ?? false, event.currentTarget.checked)}
						/>
						Also request tracks already liked before the first sync
					</label>
				{/if}

				{#if likedSyncQuery.data?.enabled}
					<div class="flex items-center justify-between gap-3 text-xs text-base-content/50">
						<span>
							{likedSyncQuery.data.requested} requested · {likedSyncQuery.data.already_in_library}
							already local
							{#if likedSyncQuery.data.unmatched > 0}
								· {likedSyncQuery.data.unmatched} unmatched{/if}
							{#if likedSyncQuery.data.failed > 0}
								· {likedSyncQuery.data.failed} failed{/if}
						</span>
						<button
							type="button"
							class="btn btn-ghost btn-xs gap-1"
							onclick={runLikedSync}
							disabled={runLikedSyncMutation.isPending}
						>
							<RefreshCw class="h-3 w-3 {runLikedSyncMutation.isPending ? 'animate-spin' : ''}" />
							Sync now
						</button>
					</div>
				{/if}

				{#if likedSyncQuery.data?.last_error}
					<p class="text-xs text-error">{likedSyncQuery.data.last_error}</p>
				{/if}
			</div>
		{/if}

		{#if error}<p class="px-1 text-xs text-error">{error}</p>{/if}
	</div>
</section>
