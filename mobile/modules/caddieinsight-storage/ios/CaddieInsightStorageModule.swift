import ExpoModulesCore
import Foundation

/**
 * Stub native module. Task 1 scaffold only — protectAndVerify / ensureProtectedRoots
 * throw until full Application Support + FileProtectionType.complete implementation lands.
 */
public class CaddieInsightStorageModule: Module {
  public func definition() -> ModuleDefinition {
    Name("CaddieInsightStorage")

    AsyncFunction("ensureProtectedRoots") { () -> [String: String] in
      throw Exception(
        name: "CaddieInsightStorageUnavailable",
        description: "CaddieInsightStorage ensureProtectedRoots is not implemented in this scaffold stub."
      )
    }

    AsyncFunction("protectAndVerify") { (_ uri: String) in
      throw Exception(
        name: "CaddieInsightStorageUnavailable",
        description: "CaddieInsightStorage protectAndVerify is not implemented in this scaffold stub."
      )
    }
  }
}
