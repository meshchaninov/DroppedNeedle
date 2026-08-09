<script lang="ts">
	import { Loader2, RefreshCw } from 'lucide-svelte';
	import SpotifyIcon from '$lib/components/SpotifyIcon.svelte';
	import { getConnectionsQuery } from '$lib/queries/connections/ConnectionsQuery.svelte';
	import {
		createConnectSpotifyMutation,
		createDisconnectMutation
	} from '$lib/queries/connections/ConnectionsMutations.svelte';
	import {
		createRunSpotifyLikedSyncMutation,
		createUpdateSpotifyLikedSyncMutation,
		getSpotifyLikedSyncQuery
	} from '$lib/queries/spotify/SpotifyLikedSyncQueries.svelte';

	const connectionsQuery = getConnectionsQuery();
	const spotify = $derived(
		connectionsQuery.data?.connections.find((c) => c.service === 'spotify') ?? null
	);

	const connectMutation = createConnectSpotifyMutation();
	const disconnectMutation = createDisconnectMutation();
	const likedSyncQuery = getSpotifyLikedSyncQuery();
	const updateLikedSyncMutation = createUpdateSpotifyLikedSyncMutation();
	const runLikedSyncMutation = createRunSpotifyLikedSyncMutation();

	let error = $state<string | null>(null);

	async function connect() {
		error = null;
		try {
			await connectMutation.mutateAsync();
		} catch {
			error = 'Could not start Spotify sign-in. Check that Spotify is configured in Settings.';
		}
	}

	async function disconnect() {
		error = null;
		await disconnectMutation.mutateAsync('spotify');
	}

	async function updateLikedSync(enabled: boolean, includeExisting?: boolean) {
		error = null;
		try {
			await updateLikedSyncMutation.mutateAsync({
				enabled,
				include_existing: includeExisting ?? likedSyncQuery.data?.include_existing ?? false
			});
		} catch {
			error = 'Could not update Spotify liked-track sync.';
		}
	}

	async function runLikedSync() {
		error = null;
		try {
			await runLikedSyncMutation.mutateAsync();
		} catch {
			error = 'Could not start Spotify liked-track sync.';
		}
	}
</script>

<section>
	<h2
		class="mb-4 flex items-center gap-2 text-sm font-semibold uppercase tracking-widest text-base-content/50"
	>
		<SpotifyIcon class="h-4 w-4 text-green-400" />
		Spotify
	</h2>

	<div
		class="space-y-3 rounded-2xl border border-base-300/50 bg-base-200/40 p-4 backdrop-blur-sm sm:p-5"
	>
		<div
			class="crate-card flex items-center justify-between gap-3 rounded-xl border border-base-300/40 bg-base-300/20 p-3"
		>
			<div class="flex min-w-0 items-center gap-3">
				<div
					class="flex h-10 w-10 items-center justify-center rounded-xl bg-green-500/10 text-green-400 ring-1 ring-green-500/20"
				>
					<SpotifyIcon class="h-[1.15rem] w-[1.15rem]" />
				</div>
				<div class="min-w-0">
					<div class="flex items-center gap-2">
						<span class="text-sm font-semibold">Spotify</span>
						<span class="status {spotify ? 'status-success' : 'status-error'} status-sm"></span>
					</div>
					{#if spotify}
						<p class="truncate text-xs text-base-content/50">@{spotify.username || 'linked'}</p>
					{:else}
						<p class="text-xs text-base-content/30">Not connected</p>
					{/if}
				</div>
			</div>

			<div class="shrink-0">
				{#if spotify}
					<button
						type="button"
						class="btn btn-ghost btn-xs rounded-full"
						onclick={disconnect}
						disabled={disconnectMutation.isPending}
					>
						{#if disconnectMutation.isPending}
							<Loader2 class="h-3.5 w-3.5 animate-spin" />
						{/if}
						Disconnect
					</button>
				{:else}
					<button
						type="button"
						class="btn btn-xs gap-1 rounded-full bg-green-600 px-3 text-white shadow-sm transition-transform hover:scale-[1.03] hover:bg-green-500"
						onclick={connect}
						disabled={connectMutation.isPending}
					>
						{#if connectMutation.isPending}
							<Loader2 class="h-3.5 w-3.5 animate-spin" />
						{:else}
							<SpotifyIcon class="h-3.5 w-3.5" />
						{/if}
						Connect
					</button>
				{/if}
			</div>
		</div>

		{#if spotify}
			<div class="space-y-3 rounded-xl border border-base-300/40 bg-base-300/10 p-3">
				<div class="flex items-start justify-between gap-4">
					<div>
						<p class="text-sm font-medium">Automatically request liked tracks</p>
						<p class="mt-0.5 text-xs text-base-content/45">
							New Spotify likes are matched with MusicBrainz and sent to your configured download
							client.
						</p>
					</div>
					<input
						type="checkbox"
						class="toggle toggle-sm toggle-success mt-0.5"
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

				{#if likedSyncQuery.data?.requires_reconnect}
					<div
						class="flex items-center justify-between gap-3 rounded-lg bg-warning/10 p-2 text-xs text-warning"
					>
						<span>Reconnect Spotify once to grant access to your liked tracks.</span>
						<button type="button" class="btn btn-warning btn-xs" onclick={connect}>Reconnect</button
						>
					</div>
				{:else if likedSyncQuery.data?.enabled}
					<div class="flex items-center justify-between gap-3 text-xs text-base-content/50">
						<span>
							{likedSyncQuery.data.requested} requested · {likedSyncQuery.data.already_in_library} already
							local
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

				{#if likedSyncQuery.data?.last_error && !likedSyncQuery.data.requires_reconnect}
					<p class="text-xs text-error">{likedSyncQuery.data.last_error}</p>
				{/if}
			</div>

			<p class="px-1 text-xs text-base-content/50">
				Connected. Go to your <a href="/playlists" class="link link-primary">Playlists</a> to import from
				Spotify.
			</p>
		{:else}
			<p class="px-1 text-xs text-base-content/40">
				Connect your Spotify account to import your personal playlists into DroppedNeedle.
			</p>
		{/if}

		{#if error}
			<p class="px-1 text-xs text-error">{error}</p>
		{/if}
	</div>
</section>
