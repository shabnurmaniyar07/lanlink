package link.lan.android.ui

import android.graphics.Bitmap
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.filled.AudioFile
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.InsertDriveFile
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Upload
import androidx.compose.material.icons.filled.VideoFile
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import link.lan.android.vm.DeviceUiState
import link.lan.app.Crumb
import link.lan.app.Sorting
import link.lan.app.bytes
import link.lan.core.Entry
import link.lan.core.Share

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun BrowseScreen(
    state: DeviceUiState,
    onBack: () -> Unit,
    onOpenShare: (Share) -> Unit,
    onOpenEntry: (Entry) -> Unit,
    onCrumb: (Crumb) -> Unit,
    onDownload: (Entry) -> Unit,
    onUpload: () -> Unit,
    onRefresh: () -> Unit,
    onSearch: (String) -> Unit,
    onSort: (Sorting) -> Unit,
    onLoadThumbnail: (suspend (String, String) -> Bitmap?)? = null,
) {
    val browse = state.browse
    var searching by remember { mutableStateOf(false) }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Column {
                        Text(
                            text = browse.title,
                            fontWeight = FontWeight.Bold,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                        )
                        if (browse.share != null) {
                            Text(
                                text = "Viewing shared folder",
                                style = MaterialTheme.typography.labelSmall,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
                    }
                },
                actions = {
                    if (browse.share != null) {
                        IconButton(onClick = { searching = !searching }) {
                            Icon(
                                if (searching) Icons.Filled.Close else Icons.Filled.Search,
                                contentDescription = if (searching) "Hide search" else "Search",
                            )
                        }
                    }
                    if (state.canUpload) {
                        IconButton(onClick = onUpload) {
                            Icon(Icons.Filled.Upload, contentDescription = "Upload here")
                        }
                    }
                    IconButton(onClick = onRefresh) {
                        Icon(Icons.Filled.Refresh, contentDescription = "Refresh")
                    }
                },
            )
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(padding),
        ) {
            if (browse.share != null) {
                Breadcrumbs(browse.crumbs, onCrumb)
                HorizontalDivider()
            }

            AnimatedVisibility(visible = searching && browse.share != null) {
                OutlinedTextField(
                    value = browse.query,
                    onValueChange = onSearch,
                    placeholder = { Text("Search files & folders…") },
                    singleLine = true,
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 16.dp, vertical = 8.dp),
                )
            }

            if (browse.share != null && browse.visible.isNotEmpty()) {
                SortRow(browse.sorting, onSort)
                HorizontalDivider()
            }

            when {
                browse.loading -> Loading()
                browse.problem != null -> Message(browse.problem.orEmpty(), MaterialTheme.colorScheme.error, "Retry", onRefresh)
                browse.share == null -> SharesList(state.shares, onOpenShare)
                browse.isEmpty && browse.query.isNotBlank() -> Message("No files matching '${browse.query}'", MaterialTheme.colorScheme.onSurfaceVariant)
                browse.isEmpty -> Message("This folder is empty", MaterialTheme.colorScheme.onSurfaceVariant)
                else -> EntriesList(
                    shareId = browse.share.id,
                    entries = browse.visible,
                    onOpen = onOpenEntry,
                    onDownload = onDownload,
                    onLoadThumbnail = onLoadThumbnail,
                )
            }
        }
    }
}

@Composable
private fun SharesList(shares: List<Share>, onOpen: (Share) -> Unit) {
    if (shares.isEmpty()) {
        Message("This device is not sharing any folders right now.", MaterialTheme.colorScheme.onSurfaceVariant)
        return
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        items(shares, key = { it.id }) { share ->
            Card(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable(enabled = share.available) { onOpen(share) },
                shape = RoundedCornerShape(14.dp),
                colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant),
            ) {
                Row(
                    modifier = Modifier.padding(16.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Box(
                        modifier = Modifier
                            .size(44.dp)
                            .clip(RoundedCornerShape(12.dp))
                            .background(MaterialTheme.colorScheme.primaryContainer),
                        contentAlignment = Alignment.Center,
                    ) {
                        Icon(
                            Icons.Filled.Folder,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onPrimaryContainer,
                            modifier = Modifier.size(24.dp),
                        )
                    }
                    Spacer(Modifier.width(14.dp))
                    Column(Modifier.weight(1f)) {
                        Text(share.name, fontWeight = FontWeight.Bold, style = MaterialTheme.typography.titleMedium)
                        Text(
                            text = permissions(share),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }
}

private fun permissions(share: Share): String = when {
    !share.available -> "Unavailable"
    share.canWrite && share.canDelete -> "Read, Write & Delete"
    share.canWrite -> "Read & Write"
    else -> "Read Only"
}

@Composable
private fun EntriesList(
    shareId: String,
    entries: List<Entry>,
    onOpen: (Entry) -> Unit,
    onDownload: (Entry) -> Unit,
    onLoadThumbnail: (suspend (String, String) -> Bitmap?)?,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(entries, key = { it.path }) { entry ->
            AdvancedEntryCard(
                shareId = shareId,
                entry = entry,
                onOpen = { onOpen(entry) },
                onDownload = { onDownload(entry) },
                onLoadThumbnail = onLoadThumbnail,
            )
        }
    }
}

@Composable
private fun AdvancedEntryCard(
    shareId: String,
    entry: Entry,
    onOpen: () -> Unit,
    onDownload: () -> Unit,
    onLoadThumbnail: (suspend (String, String) -> Bitmap?)?,
) {
    val ext = entry.name.substringAfterLast('.', "").lowercase()
    val isImage = ext in listOf("jpg", "jpeg", "png", "webp", "gif", "bmp", "heic")

    Card(
        modifier = Modifier
            .fillMaxWidth()
            .clickable { if (entry.isFolder) onOpen() else onDownload() },
        shape = RoundedCornerShape(12.dp),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f)),
    ) {
        Row(
            modifier = Modifier.padding(12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // Photo thumbnail or Media Icon Box
            if (isImage && onLoadThumbnail != null) {
                var thumbnail by remember(entry.path) { mutableStateOf(ThumbnailCache.get("$shareId:${entry.path}")) }

                LaunchedEffect(entry.path) {
                    if (thumbnail == null) {
                        thumbnail = onLoadThumbnail(shareId, entry.path)
                    }
                }

                if (thumbnail != null) {
                    Image(
                        bitmap = thumbnail!!.asImageBitmap(),
                        contentDescription = entry.name,
                        contentScale = ContentScale.Crop,
                        modifier = Modifier
                            .size(44.dp)
                            .clip(RoundedCornerShape(8.dp)),
                    )
                } else {
                    FileIconBadge(entry)
                }
            } else {
                FileIconBadge(entry)
            }

            Spacer(Modifier.width(12.dp))

            Column(Modifier.weight(1f)) {
                Text(
                    text = entry.name,
                    fontWeight = FontWeight.Medium,
                    style = MaterialTheme.typography.bodyMedium,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
                if (!entry.isFolder) {
                    Text(
                        text = entry.size?.let(::bytes) ?: "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }

            if (!entry.isFolder) {
                IconButton(onClick = onDownload) {
                    Icon(
                        Icons.Filled.Download,
                        contentDescription = "Download",
                        tint = MaterialTheme.colorScheme.primary,
                    )
                }
            }
        }
    }
}

@Composable
private fun FileIconBadge(entry: Entry) {
    val ext = entry.name.substringAfterLast('.', "").lowercase()
    val (icon, color) = when {
        entry.isFolder -> Icons.Filled.Folder to Color(0xFFFFA000)
        ext in listOf("jpg", "jpeg", "png", "webp", "gif", "bmp", "heic") -> Icons.Filled.Image to Color(0xFF4CAF50)
        ext in listOf("mp4", "mkv", "mov", "avi", "webm") -> Icons.Filled.VideoFile to Color(0xFF7C4DFF)
        ext in listOf("mp3", "flac", "wav", "m4a", "ogg", "aac") -> Icons.Filled.AudioFile to Color(0xFFE91E63)
        ext in listOf("pdf", "doc", "docx", "txt", "md") -> Icons.Filled.Description to Color(0xFF0288D1)
        ext in listOf("zip", "rar", "7z", "tar", "gz") -> Icons.Filled.InsertDriveFile to Color(0xFFFF5722)
        else -> Icons.Filled.InsertDriveFile to MaterialTheme.colorScheme.onSurfaceVariant
    }

    Box(
        modifier = Modifier
            .size(44.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(color.copy(alpha = 0.12f)),
        contentAlignment = Alignment.Center,
    ) {
        Icon(
            icon,
            contentDescription = null,
            tint = color,
            modifier = Modifier.size(24.dp),
        )
    }
}

@Composable
private fun Breadcrumbs(crumbs: List<Crumb>, onCrumb: (Crumb) -> Unit) {
    LazyRow(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        items(crumbs) { crumb ->
            TextButton(
                onClick = { onCrumb(crumb) },
                contentPadding = PaddingValues(horizontal = 8.dp, vertical = 4.dp),
            ) {
                Text(
                    text = crumb.label,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.primary,
                    maxLines = 1,
                )
            }
            if (crumb !== crumbs.last()) {
                Text("›", color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(horizontal = 2.dp))
            }
        }
    }
}

@Composable
private fun SortRow(sorting: Sorting, onSort: (Sorting) -> Unit) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 2.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text("Sort by:", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        for (option in Sorting.entries) {
            Surface(
                color = if (option == sorting) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
                shape = CircleShape,
                modifier = Modifier.clickable { onSort(option) },
            ) {
                Text(
                    text = option.name.lowercase().replaceFirstChar { it.uppercase() },
                    style = MaterialTheme.typography.labelSmall,
                    fontWeight = if (option == sorting) FontWeight.Bold else FontWeight.Normal,
                    color = if (option == sorting) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun Loading() {
    Column(
        Modifier.fillMaxSize(),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        CircularProgressIndicator()
        Spacer(Modifier.height(12.dp))
        Text("Loading files…", style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun Message(
    text: String,
    colour: Color,
    action: String? = null,
    onAction: (() -> Unit)? = null,
) {
    Column(
        Modifier
            .fillMaxSize()
            .padding(32.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(text, style = MaterialTheme.typography.bodyLarge, color = colour)
        if (action != null && onAction != null) {
            Spacer(Modifier.height(16.dp))
            TextButton(onClick = onAction) { Text(action) }
        }
    }
}
