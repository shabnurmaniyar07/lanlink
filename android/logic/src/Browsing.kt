package link.lan.app

import link.lan.core.Entry
import link.lan.core.PART_SUFFIX
import link.lan.core.Paths
import link.lan.core.Share

/** How a listing should be ordered. Folders lead in every case; people expect it. */
enum class Sorting { NAME, NEWEST, LARGEST }

/** One step in the path bar. [path] is empty at the root of a share. */
data class Crumb(val label: String, val path: String)

/**
 * Where the phone is looking, and what it may do there.
 *
 * All of it derived, none of it stored twice: the screen asks this object what
 * to draw and what to enable rather than keeping its own copy of the answer.
 * A path only ever changes through [into], [up] or [jumpTo], each of which
 * refuses anything the protocol would refuse (§9), so a hostile listing cannot
 * walk the phone out of the share it came from.
 */
data class BrowseState(
    val share: Share? = null,
    val path: String = "",
    val entries: List<Entry> = emptyList(),
    val sorting: Sorting = Sorting.NAME,
    val query: String = "",
    val loading: Boolean = false,
    val problem: String? = null,
) {
    val atRoot: Boolean get() = path.isEmpty()

    val canWrite: Boolean get() = share?.canWrite == true
    val canDelete: Boolean get() = share?.canDelete == true

    val title: String
        get() = when {
            share == null -> "Shared folders"
            path.isEmpty() -> share.name
            else -> Paths.leafOf(path)
        }

    /**
     * The path bar: the share, then every folder between it and here.
     *
     * `Paths.trail` already hands back the accumulated path at each step —
     * ["", "photos", "photos/2026"] — so the label is the leaf of each and the
     * empty one is the share itself.
     */
    val crumbs: List<Crumb>
        get() {
            val share = this.share ?: return emptyList()
            return Paths.trail(path).map { step ->
                if (step.isEmpty()) Crumb(share.name, "") else Crumb(Paths.leafOf(step), step)
            }
        }

    /**
     * What to draw: filtered, sorted, folders first.
     *
     * Half-finished uploads are hidden. A `.lanlink-part` is not a file anybody
     * asked for, and offering to download one produces a corrupt copy.
     */
    val visible: List<Entry>
        get() {
            val needle = query.trim().lowercase()
            val kept = entries.filter { entry ->
                !Paths.isPartial(entry.name) &&
                    (needle.isEmpty() || entry.name.lowercase().contains(needle))
            }
            val order: Comparator<Entry> = when (sorting) {
                Sorting.NAME -> compareBy { it.name.lowercase() }
                Sorting.NEWEST -> compareByDescending { it.modifiedAt ?: 0.0 }
                Sorting.LARGEST -> compareByDescending { it.size ?: 0L }
            }
            return kept.sortedWith(compareBy<Entry> { !it.isFolder }.then(order))
        }

    val isEmpty: Boolean get() = !loading && problem == null && visible.isEmpty()

    val emptyMessage: String
        get() = when {
            share == null -> "No shared folders yet."
            query.isNotBlank() -> "Nothing here matches \"${query.trim()}\"."
            entries.isEmpty() -> "This folder is empty."
            else -> "Nothing here matches."
        }

    // ------------------------------------------------------------- navigation

    fun openShare(share: Share): BrowseState =
        BrowseState(share = share, path = "", loading = true, sorting = sorting)

    /** Into a folder from the current listing. Refuses anything that is not one. */
    fun into(entry: Entry): BrowseState {
        if (!entry.isFolder) return this
        val next = entry.path.ifBlank { Paths.join(path, entry.name) }
        if (!Paths.isSafePath(next)) {
            return copy(problem = "That folder name cannot be opened safely.")
        }
        return copy(path = next, entries = emptyList(), query = "", loading = true, problem = null)
    }

    /** Up one level, or back to the share list when already at the root. */
    fun up(): BrowseState? {
        val share = this.share ?: return null
        if (atRoot) return null
        return copy(
            share = share,
            path = Paths.parentOf(path),
            entries = emptyList(),
            query = "",
            loading = true,
            problem = null,
        )
    }

    /** Straight to a crumb. Only ever a prefix of where we already are. */
    fun jumpTo(crumb: Crumb): BrowseState {
        if (crumb.path == path) return this
        return copy(path = crumb.path, entries = emptyList(), query = "", loading = true, problem = null)
    }

    // ---------------------------------------------------------------- loading

    fun loaded(entries: List<Entry>): BrowseState =
        copy(entries = entries, loading = false, problem = null)

    fun failed(message: String): BrowseState =
        copy(entries = emptyList(), loading = false, problem = message)

    fun searching(query: String): BrowseState = copy(query = query)

    fun sortedBy(sorting: Sorting): BrowseState = copy(sorting = sorting)

    /** True when the back gesture should leave the screen rather than go up. */
    val backLeavesTheScreen: Boolean get() = share == null || atRoot
}

/**
 * The name to save an incoming file under, without overwriting anything.
 *
 * The peer's name is sanitised first — it arrives over the network and the
 * phone is about to turn it into a real file (§21).
 */
fun downloadName(remoteName: String, taken: Set<String>): String {
    val safe = Paths.sanitiseForPeer(remoteName, fallback = "download")
    if (safe !in taken) return safe

    val extension = safe.substringAfterLast('.', "")
    val stem = if (extension.isEmpty()) safe else safe.dropLast(extension.length + 1)
    var attempt = 2
    while (true) {
        val candidate = if (extension.isEmpty()) "$stem ($attempt)" else "$stem ($attempt).$extension"
        if (candidate !in taken) return candidate
        attempt += 1
    }
}

/** The name a partial download is held under while it is still arriving. */
fun partialName(name: String): String = name + PART_SUFFIX
