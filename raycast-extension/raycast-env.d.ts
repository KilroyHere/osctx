/// <reference types="@raycast/api">

/* 🚧 🚧 🚧
 * This file is auto-generated from the extension's manifest.
 * Do not modify manually. Instead, update the `package.json` file.
 * 🚧 🚧 🚧 */

/* eslint-disable @typescript-eslint/ban-types */

type ExtensionPreferences = {
  /** Daemon URL - URL where the OSCTX daemon is running */
  "daemonUrl": string
}

/** Preferences accessible in all the extension's commands */
declare type Preferences = ExtensionPreferences

declare namespace Preferences {
  /** Preferences accessible in the `search-memory` command */
  export type SearchMemory = ExtensionPreferences & {}
}

declare namespace Arguments {
  /** Arguments passed to the `search-memory` command */
  export type SearchMemory = {}
}

