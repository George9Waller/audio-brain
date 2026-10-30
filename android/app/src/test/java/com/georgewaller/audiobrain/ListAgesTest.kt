package com.georgewaller.audiobrain

import com.georgewaller.audiobrain.data.ListContext
import com.georgewaller.audiobrain.data.MixList
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDateTime

class ListAgesTest {
    private fun list(key: String, kind: String, context: ListContext? = null) = MixList(key, kind, "lists", key, builtAt = "2026-10-30T06:30:00Z", context = context)

    private fun context(id: Long, recent: Int, active: Boolean = true, hours: List<Float> = emptyList(), eraEnd: String? = null) =
        ListContext(id, "Context $id", 0f, active = active, recentPlays = recent, hours = hours, eraStart = "2018-01-31", eraEnd = eraEnd)

    @Test
    fun sectionsGoFromNowToOlder() {
        val contexts = (1L..8L).map { list("context:$it", "mix", context(it, recent = it.toInt() * 10)) }
        val lists = listOf(
            list("year:2019", "year"), list("year:2025", "year"), list("fresh", "fresh"), list("repeat", "repeat"),
            list("context:90", "era", context(90, 3, eraEnd = "2024-05-08")), list("context:91", "era", context(91, 11, eraEnd = "2025-04-28")),
            list("context:45", "mix", context(45, 0, active = false)), list("ask:1", "asked"),
        ) + contexts
        val sections = byAge(lists).associate { (age, ls) -> age to ls.map { it.key } }
        assertEquals(ListAge.entries.toList(), sections.keys.toList())
        assertEquals((8L downTo 3L).map { "context:$it" }, sections[ListAge.Now])
        assertEquals(listOf("context:2", "context:1"), sections[ListAge.Lately])
        assertEquals(listOf("context:91", "context:90"), sections[ListAge.Era])
        assertEquals(listOf("context:45"), sections[ListAge.Kept])
        assertEquals(listOf("year:2025", "year:2019"), sections[ListAge.Year])
    }

    @Test
    fun tagsSayHowOld() {
        val era = list("context:90", "era", context(90, 3, eraEnd = "2024-05-08"))
        assertEquals("2018–24", ListAge.Era.tag(era).label)
        assertTrue(ListAge.Now.tag(era).current)
        assertEquals("2018", eraSpan("2018-01-01", "2018-12-01"))
        assertNull(eraSpan(null, "2024-01-01"))
    }

    @Test
    fun fitsNowPrefersWhatYouPlayAtThisHour() {
        val morning = List(24) { if (it in 9..11) 0.2f else 0.4f / 21 }
        val evening = List(24) { if (it in 19..21) 0.2f else 0.4f / 21 }
        val lists = listOf(
            list("context:1", "mix", context(1, recent = 20, hours = evening)),
            list("context:2", "mix", context(2, recent = 20, hours = morning)),
            list("context:3", "mix", context(3, recent = 20, active = false, hours = morning)),
            list("context:4", "era", context(4, recent = 20, hours = morning)),
        )
        val fits = fitsNow(lists, LocalDateTime.of(2026, 10, 30, 10, 0))
        assertEquals(listOf("context:2", "context:1"), fits.map { it.list.key })
        assertTrue(fits[0].reason, fits[0].reason.startsWith("4.8× your usual around 10:00"))
    }
}
