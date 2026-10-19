package com.georgewaller.audiobrain.data

import android.content.Context
import androidx.room.Dao
import androidx.room.Database
import androidx.room.Entity
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.PrimaryKey
import androidx.room.Query
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import kotlinx.coroutines.flow.Flow

/**
 * A verdict as recorded on the device: written first, sent later. [state] is pending until the server has it
 * (sent, with the track it landed on) or turns it down for good (rejected, with why).
 */
@Entity(tableName = "feedback")
data class FeedbackEntity(
    @PrimaryKey val clientId: String,
    val verdict: String,
    val trackKey: String,
    val trackId: Long?,
    val tidalId: String?,
    val artist: String,
    val title: String,
    val album: String,
    val durationMs: Long?,
    val packageName: String,
    val where: String,
    val contextId: Long?,
    val givenAt: Long,
    val state: String = PENDING,
    val serverTrackId: Long? = null,
    val error: String? = null,
    val attempts: Int = 0,
) {
    val ref get() = TrackRef(trackId, tidalId, artist, title, album, durationMs, packageName)

    companion object {
        const val PENDING = "pending"
        const val SENT = "sent"
        const val REJECTED = "rejected"
    }
}

@Dao
interface FeedbackDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(item: FeedbackEntity)

    @Query("SELECT * FROM feedback WHERE state = 'pending' ORDER BY givenAt LIMIT :limit")
    suspend fun pending(limit: Int): List<FeedbackEntity>

    @Query("UPDATE feedback SET state = 'sent', serverTrackId = :trackId, error = NULL WHERE clientId = :clientId")
    suspend fun markSent(clientId: String, trackId: Long)

    @Query("UPDATE feedback SET state = 'rejected', error = :reason WHERE clientId = :clientId")
    suspend fun markRejected(clientId: String, reason: String)

    @Query("UPDATE feedback SET attempts = attempts + 1, error = :error WHERE clientId IN (:ids)")
    suspend fun failedAttempt(ids: List<String>, error: String)

    /** The latest verdict given on this device for a track, to show it as already chosen. */
    @Query("SELECT * FROM feedback WHERE trackKey = :trackKey ORDER BY givenAt DESC LIMIT 1")
    fun latestFor(trackKey: String): Flow<FeedbackEntity?>

    @Query("SELECT * FROM feedback WHERE trackKey = :trackKey ORDER BY givenAt DESC LIMIT 1")
    suspend fun latestForNow(trackKey: String): FeedbackEntity?

    @Query("DELETE FROM feedback WHERE clientId = :clientId AND state = 'pending'")
    suspend fun deletePending(clientId: String): Int

    @Query("SELECT COUNT(*) FROM feedback WHERE state = 'pending'")
    fun pendingCount(): Flow<Int>

    @Query("SELECT * FROM feedback ORDER BY givenAt DESC LIMIT :limit")
    fun recent(limit: Int): Flow<List<FeedbackEntity>>

    @Query("SELECT trackKey, verdict FROM feedback f WHERE givenAt = (SELECT MAX(givenAt) FROM feedback WHERE trackKey = f.trackKey)")
    fun latestAll(): Flow<List<KeyVerdict>>
}

/** A discovery-queue decision on a release (saved, dismissed), queued like a verdict. */
@Entity(tableName = "queue")
data class QueueEntity(
    @PrimaryKey val clientId: String,
    val releaseId: Long,
    val state: String,
    val givenAt: Long,
    val sent: Boolean = false,
)

@Dao
interface QueueDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(item: QueueEntity)

    @Query("SELECT * FROM queue WHERE sent = 0 ORDER BY givenAt LIMIT :limit")
    suspend fun pending(limit: Int): List<QueueEntity>

    @Query("UPDATE queue SET sent = 1 WHERE clientId = :clientId")
    suspend fun markSent(clientId: String)

    @Query("DELETE FROM queue WHERE clientId = :clientId")
    suspend fun delete(clientId: String)

    /** The latest decision per release, to show it on the row before the server knows. */
    @Query("SELECT * FROM queue q WHERE givenAt = (SELECT MAX(givenAt) FROM queue WHERE releaseId = q.releaseId)")
    fun latest(): Flow<List<QueueEntity>>

    @Query("SELECT COUNT(*) FROM queue WHERE sent = 0")
    fun pendingCount(): Flow<Int>
}

@Entity(tableName = "mix_rating")
data class MixRatingEntity(@PrimaryKey val clientId: String, val key: String, val rating: Int, val note: String, val givenAt: Long, val sent: Boolean = false)

@Entity(tableName = "comparison")
data class ComparisonEntity(@PrimaryKey val clientId: String, val leftId: Long, val rightId: Long, val preferred: String, val key: String, val givenAt: Long, val sent: Boolean = false)

@Dao
interface RatingDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun upsert(item: MixRatingEntity)
    @Query("SELECT * FROM mix_rating WHERE sent = 0 ORDER BY givenAt LIMIT :limit") suspend fun pending(limit: Int): List<MixRatingEntity>
    @Query("UPDATE mix_rating SET sent = 1 WHERE clientId = :clientId") suspend fun markSent(clientId: String)
}

@Dao
interface ComparisonDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE) suspend fun upsert(item: ComparisonEntity)
    @Query("SELECT * FROM comparison WHERE sent = 0 ORDER BY givenAt LIMIT :limit") suspend fun pending(limit: Int): List<ComparisonEntity>
    @Query("UPDATE comparison SET sent = 1 WHERE clientId = :clientId") suspend fun markSent(clientId: String)
}

/** The latest verdict per track key, for colouring rows anywhere in the app. */
data class KeyVerdict(val trackKey: String, val verdict: String)

@Database(entities = [FeedbackEntity::class, QueueEntity::class, MixRatingEntity::class, ComparisonEntity::class], version = 3)
abstract class OutboxDatabase : RoomDatabase() {
    abstract fun feedback(): FeedbackDao
    abstract fun queue(): QueueDao
    abstract fun ratings(): RatingDao
    abstract fun comparisons(): ComparisonDao

    companion object {
        private val ADD_QUEUE = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("CREATE TABLE IF NOT EXISTS `queue` (`clientId` TEXT NOT NULL, `releaseId` INTEGER NOT NULL, `state` TEXT NOT NULL, `givenAt` INTEGER NOT NULL, `sent` INTEGER NOT NULL, PRIMARY KEY(`clientId`))")
            }
        }

        private val ADD_RATINGS = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL("CREATE TABLE IF NOT EXISTS `mix_rating` (`clientId` TEXT NOT NULL, `key` TEXT NOT NULL, `rating` INTEGER NOT NULL, `note` TEXT NOT NULL, `givenAt` INTEGER NOT NULL, `sent` INTEGER NOT NULL, PRIMARY KEY(`clientId`))")
                db.execSQL("CREATE TABLE IF NOT EXISTS `comparison` (`clientId` TEXT NOT NULL, `leftId` INTEGER NOT NULL, `rightId` INTEGER NOT NULL, `preferred` TEXT NOT NULL, `key` TEXT NOT NULL, `givenAt` INTEGER NOT NULL, `sent` INTEGER NOT NULL, PRIMARY KEY(`clientId`))")
            }
        }

        fun create(context: Context) = Room.databaseBuilder(context, OutboxDatabase::class.java, "outbox.db").addMigrations(ADD_QUEUE, ADD_RATINGS).build()
    }
}
