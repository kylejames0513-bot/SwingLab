package expo.modules.caddieinsightstorage

import expo.modules.kotlin.modules.Module
import expo.modules.kotlin.modules.ModuleDefinition
import expo.modules.kotlin.exception.CodedException

/**
 * Stub native module. Task 1 scaffold — fail closed until noBackupFilesDir roots land.
 */
class CaddieInsightStorageModule : Module() {
  override fun definition() = ModuleDefinition {
    Name("CaddieInsightStorage")

    AsyncFunction("ensureProtectedRoots") {
      throw CodedException(
        "CaddieInsightStorageUnavailable",
        "CaddieInsightStorage ensureProtectedRoots is not implemented in this scaffold stub.",
        null
      )
    }

    AsyncFunction("protectAndVerify") { _: String ->
      throw CodedException(
        "CaddieInsightStorageUnavailable",
        "CaddieInsightStorage protectAndVerify is not implemented in this scaffold stub.",
        null
      )
    }
  }
}
