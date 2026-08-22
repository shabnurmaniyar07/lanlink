package link.lan.android.server

import android.content.Context
import android.net.Uri
import androidx.documentfile.provider.DocumentFile
import link.lan.core.Json
import link.lan.core.Paths
import link.lan.core.boolean
import link.lan.core.objects
import link.lan.core.string
import java.io.FileNotFoundException
import java.util.UUID

data class LocalShare(
    val id: String,
    val name: String,
    val treeUri: String,
    val writable: Boolean = true,
    val removable: Boolean = true,
) {
    fun toPublicShare(): Map<String, Any> = mapOf(
        "id" to id,
        "name" to name,
        "path" to name,
        "writable" to writable,
        "removable" to removable,
        "available" to true,
    )

    companion object {
        fun from(values: Map<String, Any?>): LocalShare = LocalShare(
            id = values.string("id"),
            name = values.string("name"),
            treeUri = values.string("tree_uri"),
            writable = values.boolean("writable", true),
            removable = values.boolean("removable", true),
        )
    }
}

class LocalShareStore(private val context: Context) {
    private val prefs = context.getSharedPreferences("lanlink_local_shares", Context.MODE_PRIVATE)

    fun all(): List<LocalShare> {
        val raw = prefs.getString(SHARES_KEY, null) ?: return emptyList()
        return try {
            val root = Json.parseObject(raw)
            root.objects("shares").map { LocalShare.from(it) }
        } catch (_: Exception) {
            emptyList()
        }
    }

    fun find(id: String): LocalShare? = all().firstOrNull { it.id == id }

    fun add(name: String, treeUri: Uri, writable: Boolean = true, removable: Boolean = true): LocalShare {
        val shares = all().toMutableList()
        val id = UUID.randomUUID().toString()
        val share = LocalShare(id, name.trim(), treeUri.toString(), writable, removable)
        shares.add(share)
        save(shares)
        return share
    }

    fun remove(id: String): Boolean {
        val shares = all().toMutableList()
        val removed = shares.removeAll { it.id == id }
        if (removed) save(shares)
        return removed
    }

    private fun save(shares: List<LocalShare>) {
        val entries = shares.joinToString(",") { share ->
            """{"id":"${share.id}","name":${Json.quote(share.name)},"tree_uri":${Json.quote(share.treeUri)},"writable":${share.writable},"removable":${share.removable}}"""
        }
        val json = """{"shares":[$entries]}"""
        prefs.edit().putString(SHARES_KEY, json).apply()
    }

    fun resolve(share: LocalShare, relativePath: String): DocumentFile? {
        val problem = Paths.pathProblem(relativePath)
        if (problem != null && relativePath.isNotEmpty()) return null
        val root = DocumentFile.fromTreeUri(context, Uri.parse(share.treeUri)) ?: return null
        if (relativePath.isEmpty()) return root
        val parts = relativePath.replace('\\', '/').split('/').filter { it.isNotEmpty() }
        var current: DocumentFile = root
        for (part in parts) {
            current = current.findFile(part) ?: return null
        }
        return current
    }

    fun listFolder(share: LocalShare, relativePath: String): List<Map<String, Any>> {
        val folder = resolve(share, relativePath)
            ?: throw FileNotFoundException("The folder was not found.")
        if (!folder.isDirectory) throw IllegalArgumentException("The path is not a folder.")
        val files = folder.listFiles()
        return files.mapNotNull { file ->
            val name = file.name ?: return@mapNotNull null
            if (Paths.isPartial(name)) return@mapNotNull null
            val isDir = file.isDirectory
            val childPath = Paths.join(relativePath, name)
            mapOf(
                "name" to name,
                "path" to childPath,
                "is_dir" to isDir,
                "size" to if (isDir) 0L else file.length(),
                "modified" to (file.lastModified() / 1000L),
                "extension" to (if (isDir) "" else name.substringAfterLast('.', "").lowercase()),
            )
        }.sortedWith(compareByDescending<Map<String, Any>> { it["is_dir"] as Boolean }.thenBy { (it["name"] as String).lowercase() })
    }

    companion object {
        private const val SHARES_KEY = "shares_json"
    }
}
