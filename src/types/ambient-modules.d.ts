declare module '*?raw' {
  const content: string;
  export default content;
}

declare module '*?url' {
  const url: string;
  export default url;
}

declare module '../../server/sql/schema.sql?raw' {
  const content: string;
  export default content;
}

declare module '@tauri-apps/api/path' {
  export function resourceDir(): Promise<string>;
  export function join(...parts: string[]): Promise<string>;
}

declare module '@tauri-apps/plugin-fs' {
  export function readTextFile(path: string): Promise<string>;
}

declare module '@tauri-apps/plugin-sql' {
  export interface DatabaseConnection {
    execute(query: string, bindValues?: unknown[]): Promise<unknown>;
    select<T>(query: string, bindValues?: unknown[]): Promise<T[]>;
  }

  const Database: {
    load(connection: string): Promise<DatabaseConnection>;
  };

  export default Database;
}