package link.lan.android.ui

import android.graphics.Bitmap
import androidx.collection.LruCache

object ThumbnailCache {
    private val memoryCache = LruCache<String, Bitmap>(100)

    fun get(key: String): Bitmap? = memoryCache.get(key)

    fun put(key: String, bitmap: Bitmap) {
        memoryCache.put(key, bitmap)
    }

    fun clear() {
        memoryCache.evictAll()
    }
}
