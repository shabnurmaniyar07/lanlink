package link.lan.core.test

/**
 * A very small test harness.
 *
 * JUnit would mean a Maven dependency, and this container cannot reach one. The
 * suite is small and the reporting only has to be good enough for a person and
 * for pytest to parse, so a hundred lines here beats a dependency.
 */

class Failure(message: String) : AssertionError(message)

data class Outcome(val name: String, val ok: Boolean, val detail: String = "")

object Suite {
    private val outcomes = mutableListOf<Outcome>()
    private var group = ""

    fun group(name: String) {
        group = name
    }

    fun test(name: String, body: () -> Unit) {
        val label = if (group.isEmpty()) name else "$group :: $name"
        try {
            body()
            outcomes.add(Outcome(label, true))
        } catch (failure: AssertionError) {
            outcomes.add(Outcome(label, false, failure.message ?: "assertion failed"))
        } catch (error: Throwable) {
            outcomes.add(Outcome(label, false, "${error::class.simpleName}: ${error.message}"))
        }
    }

    fun report(): Int {
        val failed = outcomes.filter { !it.ok }
        for (outcome in outcomes) {
            if (outcome.ok) {
                println("ok   ${outcome.name}")
            } else {
                println("FAIL ${outcome.name}")
                println("     ${outcome.detail}")
            }
        }
        println("${outcomes.size - failed.size}/${outcomes.size} kotlin checks passed")
        return if (failed.isEmpty()) 0 else 1
    }
}

fun assertTrue(condition: Boolean, message: String) {
    if (!condition) throw Failure(message)
}

fun assertFalse(condition: Boolean, message: String) = assertTrue(!condition, message)

fun <T> assertEquals(expected: T, actual: T, message: String = "") {
    if (expected != actual) {
        val label = if (message.isEmpty()) "" else "$message: "
        throw Failure("${label}expected <$expected> but got <$actual>")
    }
}

fun assertNull(value: Any?, message: String = "expected null") {
    if (value != null) throw Failure("$message, got <$value>")
}

fun assertNotNull(value: Any?, message: String = "expected a value") {
    if (value == null) throw Failure(message)
}

inline fun <reified T : Throwable> assertThrows(message: String, body: () -> Unit) {
    try {
        body()
    } catch (error: Throwable) {
        if (error is T) return
        throw Failure("$message: expected ${T::class.simpleName} but got ${error::class.simpleName}")
    }
    throw Failure("$message: nothing was thrown")
}
