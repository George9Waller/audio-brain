package com.georgewaller.audiobrain

import com.georgewaller.audiobrain.data.ListContext
import com.georgewaller.audiobrain.data.MixList
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import com.georgewaller.audiobrain.design.swatchCells

class ListAgesTest {
    private fun list(key: String, kind: String, context: ListContext? = null, builtAt: String = "2026-10-30T06:30:00Z") = MixList(key, kind, "lists", key, builtAt = builtAt, context = context)

    private fun context(id: Long, recent: Int, active: Boolean = true, hours: List<Float> = emptyList(), eraEnd: String? = null) =
        ListContext(id, "Context $id", 0f, active = active, recentPlays = recent, hours = hours, eraStart = "2018-01-31", eraEnd = eraEnd)

    @Test
    fun sectionsGoFromNowToOlder() {
        val contexts = (1L..8L).map { list("context:$it", "mix", context(it, recent = it.toInt() * 10)) }
        val lists = listOf(
            list("year:2019", "year"), list("year:2025", "year"), list("fresh", "fresh"), list("repeat", "repeat"),
            list("context:90", "era", context(90, 3, eraEnd = "2024-05-08")), list("context:91", "era", context(91, 11, eraEnd = "2025-04-28")),
            list("context:45", "mix", context(45, 0, active = false)), list("ask:1", "asked"), list("radio:5", "radio", builtAt = "2026-10-31T09:00:00Z"),
            list("set:7", "set"), list("set:12", "set"),
        ) + contexts
        val sections = byAge(lists).associate { (age, ls) -> age to ls.map { it.key } }
        assertEquals(ListAge.entries.toList(), sections.keys.toList())
        assertEquals((8L downTo 3L).map { "context:$it" }, sections[ListAge.Now])
        assertEquals(listOf("context:2", "context:1"), sections[ListAge.Lately])
        assertEquals(listOf("context:91", "context:90"), sections[ListAge.Era])
        assertEquals(listOf("context:45"), sections[ListAge.Kept])
        assertEquals(listOf("radio:5", "ask:1"), sections[ListAge.Yours])
        assertEquals(listOf("set:12", "set:7"), sections[ListAge.Sets])
        assertEquals(listOf("year:2025", "year:2019"), sections[ListAge.Year])
    }

    @Test
    fun tagsSayHowOld() {
        val era = list("context:90", "era", context(90, 3, eraEnd = "2024-05-08"))
        assertEquals("2018–24", ListAge.Era.tag(era)?.label)
        assertNull(ListAge.Yours.tag(era))
        assertEquals(listOf(10f, 10f, 10f, 20f), swatchCells(listOf(10f, null, 10f, null, 10f, 20f, null), 4))
        assertEquals(listOf(null, null), swatchCells(listOf(null, null, null), 2))
        assertEquals("2018", eraSpan("2018-01-01", "2018-12-01"))
        assertNull(eraSpan(null, "2024-01-01"))
    }
}
