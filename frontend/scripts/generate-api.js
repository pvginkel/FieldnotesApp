#!/usr/bin/env node

import { execSync } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import { fetchAndCache } from './fetch-openapi.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const OUTPUT_DIR = path.join(__dirname, '../src/lib/api/generated');
const TYPES_FILE = path.join(OUTPUT_DIR, 'types.ts');
const CLIENT_FILE = path.join(OUTPUT_DIR, 'client.ts');
const HOOKS_FILE = path.join(OUTPUT_DIR, 'hooks.ts');
const ROLES_FILE = path.join(OUTPUT_DIR, 'roles.ts');
const ROLE_MAP_FILE = path.join(OUTPUT_DIR, 'role-map.json');

/**
 * Ensures output directory exists
 */
function ensureOutputDir() {
  try {
    mkdirSync(OUTPUT_DIR, { recursive: true });
  } catch (error) {
    // Directory might already exist, ignore error
  }
}

/**
 * Generates TypeScript types from OpenAPI spec
 */
function generateTypes() {
  console.log('🔄 Generating TypeScript types...');

  // Write spec to temporary file for openapi-typescript
  const specFile = path.join(__dirname, '..', 'openapi-cache', 'openapi.json');

  try {
    // Run openapi-typescript to generate types
    execSync(`npx openapi-typescript ${specFile} --output ${TYPES_FILE}`, {
      stdio: 'inherit'
    });

    console.log('✅ TypeScript types generated');
  } catch (error) {
    throw new Error(`Failed to generate TypeScript types: ${error.message}`);
  }
}

/**
 * Generates openapi-fetch client with auth middleware
 */
function generateClient() {
  console.log('🔄 Generating API client...');

  const clientContent = `// Generated API client - do not edit manually
import createClient, { type Middleware } from 'openapi-fetch';
import type { paths } from './types';
import { buildLoginUrl } from '@/lib/auth-redirect';

// Middleware to intercept 401 responses and redirect to login
const authMiddleware: Middleware = {
  async onResponse({ response }) {
    if (response.status === 401) {
      // Redirect to login, preserving the current URL
      window.location.href = buildLoginUrl();
    }
    return response;
  },
};

// Create the main API client
export const api = createClient<paths>({
  baseUrl: '',
});

// Register auth middleware
api.use(authMiddleware);

// Export types for convenience
export type * from './types';
`;

  writeFileSync(CLIENT_FILE, clientContent);
  console.log('✅ API client generated');
}

/**
 * Generates TanStack Query hooks from OpenAPI spec
 */
function generateHooks(spec) {
  console.log('🔄 Generating TanStack Query hooks...');

  const hooks = [];
  const imports = new Set(['useQuery', 'useMutation', 'useQueryClient']);

  // Generate type aliases from schema titles
  const { typeAliases, typeMap, parameterTypeMap } = generateTypeAliases(spec);

  // Process each path in the OpenAPI spec
  for (const [path, pathItem] of Object.entries(spec.paths || {})) {
    for (const [method, operation] of Object.entries(pathItem)) {
      if (!operation.operationId) continue;

      const operationId = operation.operationId;
      const summary = operation.summary || '';
      const isQuery = method.toLowerCase() === 'get';
      const isMutation = ['post', 'put', 'patch', 'delete'].includes(method.toLowerCase());

      if (isQuery) {
        hooks.push(generateQueryHook(path, method, operation, operationId, summary, spec, typeMap, parameterTypeMap));
      } else if (isMutation) {
        hooks.push(generateMutationHook(path, method, operation, operationId, summary, spec, typeMap, parameterTypeMap));
      }
    }
  }

  const hooksContent = `// Generated TanStack Query hooks - do not edit manually
import { ${Array.from(imports).join(', ')} } from '@tanstack/react-query';
import { toApiError } from '@/lib/api/api-error';
import { api } from './client';
import type { paths, components } from './types';

// Type aliases for better developer experience
${typeAliases.join('\n')}

${hooks.join('\n\n')}
`;

  writeFileSync(HOOKS_FILE, hooksContent);
  console.log('✅ TanStack Query hooks generated');
}

/**
 * Generates a React Query hook for GET requests
 */
function generateQueryHook(path, method, operation, operationId, summary, spec, typeMap, parameterTypeMap) {
  const transformedOperationId = transformOperationId(operationId);
  const hookName = `use${capitalize(transformedOperationId)}`;
  const pathParams = extractPathParams(path);
  const hasParams = pathParams.length > 0 || (operation.parameters && operation.parameters.length > 0);

  // Assume all endpoints support query parameters
  const supportsQueryParams = true;

  let paramsType = 'void';
  let paramsArg = '';
  let pathWithParams = `'${path}' as const`;
  let queryOptions = '';

  if (hasParams || supportsQueryParams) {
    if (hasParams && supportsQueryParams) {
      // Has both path/formal params AND supports runtime query params
      paramsType = 'any';
    } else if (hasParams) {
      const parameterTypeAlias = parameterTypeMap.get(`${path}:${method}`);
      paramsType = parameterTypeAlias || `paths['${path}']['${method}']['parameters']`;
    } else if (supportsQueryParams) {
      // For endpoints that support query params but don't have them in the spec
      paramsType = 'any';
    }

    paramsArg = `params?: ${paramsType}`;

    if (pathParams.length > 0) {
      pathWithParams = `'${path}'`;
    }

    queryOptions = ', { params }';
  }

  const optionsType = (hasParams || supportsQueryParams)
    ? `Omit<Parameters<typeof useQuery>[0], 'queryKey' | 'queryFn'>`
    : `Omit<Parameters<typeof useQuery>[0], 'queryKey' | 'queryFn'>`;

  const responseType = getFriendlyType(spec, path, method, 'response', typeMap);

  return `/**
 * ${summary || `${method.toUpperCase()} ${path}`}
 */
export function ${hookName}(${paramsArg}${(hasParams || supportsQueryParams) ? ', ' : ''}options?: ${optionsType}): ReturnType<typeof useQuery<${responseType}>> {
  // @ts-ignore
  return useQuery({
    queryKey: ['${transformedOperationId}'${(hasParams || supportsQueryParams) ? ', params' : ''}],
    queryFn: async () => {
      const result = await api.${method.toUpperCase()}(${pathWithParams}${queryOptions}) as { data?: unknown; error?: unknown; response: Response };
      if (result.error) throw toApiError(result.error, result.response.status);
      return result.data;
    },
    ...options
  });
}`;
}

/**
 * Generates a React Query mutation hook for POST/PUT/PATCH/DELETE requests
 */
function generateMutationHook(path, method, operation, operationId, summary, spec, typeMap, parameterTypeMap) {
  const transformedOperationId = transformOperationId(operationId);
  const hookName = `use${capitalize(transformedOperationId)}`;
  const pathParams = extractPathParams(path);
  const hasBody = operation.requestBody;
  const hasPathParams = pathParams.length > 0;
  // Check for query parameters in the operation
  const hasQueryParams = operation.parameters && operation.parameters.some(p => p.in === 'query');

  let variablesType = 'void';
  let pathWithParams = `'${path}' as const`;
  let mutationArgs = '';

  if (hasPathParams || hasBody || hasQueryParams) {
    const parts = [];
    if (hasPathParams) {
      const parameterTypeAlias = parameterTypeMap.get(`${path}:${method}`);
      if (parameterTypeAlias) {
        parts.push(`path: ${parameterTypeAlias}['path']`);
      } else {
        parts.push(`path: paths['${path}']['${method}']['parameters']['path']`);
      }
    }
    if (hasQueryParams) {
      const parameterTypeAlias = parameterTypeMap.get(`${path}:${method}`);
      if (parameterTypeAlias) {
        parts.push(`query: ${parameterTypeAlias}['query']`);
      } else {
        parts.push(`query: paths['${path}']['${method}']['parameters']['query']`);
      }
    }
    if (hasBody) {
      const bodyType = getFriendlyType(spec, path, method, 'requestBody', typeMap);
      parts.push(`body: ${bodyType}`);
    }
    variablesType = `{ ${parts.join('; ')} }`;

    if (hasPathParams) {
      pathWithParams = `'${path}'`;
    }

    // Build mutation arguments
    const argParts = [];
    if (hasPathParams || hasQueryParams) {
      const paramParts = [];
      if (hasPathParams) paramParts.push('path: variables.path');
      if (hasQueryParams) paramParts.push('query: variables.query');
      argParts.push(`params: { ${paramParts.join(', ')} }`);
    }
    if (hasBody) {
      argParts.push('body: variables.body');
    }
    mutationArgs = argParts.length > 0 ? `, { ${argParts.join(', ')} }` : '';
  }

  const responseType = getFriendlyType(spec, path, method, 'response', typeMap);

  return `/**
 * ${summary || `${method.toUpperCase()} ${path}`}
 */
export function ${hookName}(options?: Omit<Parameters<typeof useMutation>[0], 'mutationFn'>): ReturnType<typeof useMutation<${responseType}, Error, ${variablesType}>> {
  const queryClient = useQueryClient();

  // @ts-ignore
  return useMutation({
    mutationFn: async (${(variablesType == 'void' ? '' : 'variables: ' + variablesType)}) => {
      const result = await api.${method.toUpperCase()}(${pathWithParams}${mutationArgs}) as { data?: unknown; error?: unknown; response: Response };
      if (result.error) throw toApiError(result.error, result.response.status);
      return result.data;
    },
    onSuccess: () => {
      // Invalidate relevant queries after successful mutation
      queryClient.invalidateQueries();
    },
    ...options
  });
}`;
}

/**
 * Extracts path parameters from an OpenAPI path
 */
function extractPathParams(path) {
  const matches = path.match(/\{([^}]+)\}/g);
  return matches ? matches.map(match => match.slice(1, -1)) : [];
}

/**
 * Capitalizes the first letter of a string
 */
function capitalize(str) {
  return str.charAt(0).toUpperCase() + str.slice(1);
}

/**
 * Extracts parameter names from operationId
 */
function extractParameters(operationId) {
  const matches = operationId.match(/\{([^}]+)\}/g);
  return matches ? matches.map(match => match.slice(1, -1)) : [];
}

/**
 * Converts underscore-separated strings to camelCase
 */
function toCamelCase(str) {
  return str.replace(/_([a-z0-9])/g, (_, letter) => letter.toUpperCase());
}

/**
 * Transforms operationId using custom naming pattern
 */
function transformOperationId(operationId) {
  // Extract parameters
  const parameters = extractParameters(operationId);

  // Replace parameter patterns with single underscore and remove __api
  let baseName = operationId.replace(/\{[^}]+\}/g, '_');
  baseName = baseName.replace(/__api/g, '');

  // Replace hyphens and dots with underscores
  baseName = baseName.replace(/[-\.]/g, '_');

  // Clean up multiple consecutive underscores
  baseName = baseName.replace(/_+/g, '_');
  baseName = baseName.replace(/^_|_$/g, ''); // Remove leading/trailing underscores

  // Create parameter suffix if parameters exist
  let parameterSuffix = '';
  if (parameters.length > 0) {
    parameterSuffix = '_by_' + parameters.join('_and_');
  }

  // Concatenate base name + parameter suffix
  const fullName = baseName + parameterSuffix;

  // Convert to camelCase
  return toCamelCase(fullName);
}

/**
 * Generates user-friendly type aliases from OpenAPI schemas and parameters
 */
function generateTypeAliases(spec) {
  const typeAliases = [];
  const typeMap = new Map();
  const parameterTypeMap = new Map();

  // Extract schemas and create aliases based on unique schema keys
  if (spec.components && spec.components.schemas) {
    for (const [schemaKey, schema] of Object.entries(spec.components.schemas)) {
      if (schema.title) {
        // Convert schema key to a valid TypeScript identifier
        // Replace dots and special characters with underscores
        const aliasName = schemaKey.replace(/[^a-zA-Z0-9]/g, '_');
        const longTypePath = `components['schemas']['${schemaKey}']`;

        typeAliases.push(`export type ${aliasName} = ${longTypePath};`);
        typeMap.set(schemaKey, aliasName);
      }
    }
  }

  // Generate parameter type aliases for each operation
  for (const [path, pathItem] of Object.entries(spec.paths || {})) {
    for (const [method, operation] of Object.entries(pathItem)) {
      if (!operation.operationId) continue;

      const transformedOperationId = transformOperationId(operation.operationId);
      const pathParams = extractPathParams(path);
      const hasParams = pathParams.length > 0 || (operation.parameters && operation.parameters.length > 0);

      if (hasParams) {
        const parameterTypeName = `${capitalize(transformedOperationId)}Parameters`;
        const parameterTypePath = `paths['${path}']['${method}']['parameters']`;

        typeAliases.push(`export type ${parameterTypeName} = ${parameterTypePath};`);
        parameterTypeMap.set(`${path}:${method}`, parameterTypeName);
      }
    }
  }

  return { typeAliases, typeMap, parameterTypeMap };
}

/**
 * Finds the schema reference for a specific path/method/type combination
 */
function findSchemaReference(spec, path, method, type) {
  try {
    const pathItem = spec.paths[path];
    if (!pathItem) return null;

    const operation = pathItem[method];
    if (!operation) return null;

    if (type === 'response') {
      // Check for success response codes (200-299)
      if (operation.responses) {
        const responseCodes = Object.keys(operation.responses)
          .map(code => parseInt(code, 10))
          .filter(code => !isNaN(code) && code >= 200 && code < 300)
          .sort((a, b) => a - b);

        for (const code of responseCodes) {
          const response = operation.responses[code.toString()];
          const schema = response.content?.['application/json']?.schema;
          if (schema && schema.$ref) {
            return {
              schemaRef: schema.$ref.replace('#/components/schemas/', ''),
              statusCode: code.toString()
            };
          }
        }
      }
    } else if (type === 'requestBody') {
      const requestBody = operation.requestBody;
      const schema = requestBody?.content?.['application/json']?.schema;
      if (schema && schema.$ref) {
        return {
          schemaRef: schema.$ref.replace('#/components/schemas/', ''),
          statusCode: null
        };
      }
    }
  } catch (error) {
    // Ignore errors and fall back to long path
  }

  return null;
}

/**
 * Gets a friendly type alias or falls back to the original type path
 */
function getFriendlyType(spec, path, method, type, typeMap) {
  const result = findSchemaReference(spec, path, method, type);

  if (result && result.schemaRef && typeMap.has(result.schemaRef)) {
    return typeMap.get(result.schemaRef);
  }

  // For responses, check if any success response exists, otherwise assume void
  if (type === 'response') {
    const operation = spec.paths[path]?.[method];
    if (operation?.responses) {
      // Get actual response codes, filter for success (200-299), and sort
      const responseCodes = Object.keys(operation.responses)
        .map(code => parseInt(code, 10))
        .filter(code => !isNaN(code) && code >= 200 && code < 300)
        .sort((a, b) => a - b);

      for (const code of responseCodes) {
        const response = operation.responses[code.toString()];
        if (response.content?.['application/json']) {
          // Has content, use the long path with the actual status code
          return `NonNullable<paths['${path}']['${method}']['responses']['${code}']['content']['application/json']>`;
        } else {
          // No content, return void for operations that don't return data
          return 'void';
        }
      }
    }
    // No success response found, assume void
    return 'void';
  } else if (type === 'requestBody') {
    return `NonNullable<paths['${path}']['${method}']['requestBody']>['content']['application/json']`;
  }

  return 'void';
}

/**
 * Derives the role constant name from a hook name.
 * Strips the "use" prefix, lowercases the first character, and appends "Role".
 * Example: "useDeleteItemsByItemId" -> "deleteItemsByItemIdRole"
 */
function hookNameToRoleConstant(hookName) {
  const withoutUse = hookName.slice(3); // strip "use"
  return withoutUse.charAt(0).toLowerCase() + withoutUse.slice(1) + 'Role';
}

/**
 * Generates role constants (roles.ts) and hook-to-role mapping (role-map.json)
 * from x-required-role annotations in the OpenAPI spec.
 *
 * Only endpoints whose x-required-role differs from the baseline read role
 * (determined by x-auth-roles.read) get constants generated. The role map
 * only includes mutation hooks (POST/PUT/PATCH/DELETE) since GET hooks
 * correspond to reader-level access.
 */
function generateRoles(spec) {
  console.log('🔄 Generating role constants and role map...');

  // Determine the baseline read role from the spec root
  const readRole = spec['x-auth-roles']?.read;
  if (!readRole) {
    console.warn('⚠️  x-auth-roles.read not found in spec; skipping role generation.');
    return;
  }

  // Collect role constants and the mutation hook-to-constant mapping
  const roleConstants = []; // { constantName, roleValue }
  const roleMap = {};       // hookName -> constantName
  const roleValues = new Set();

  for (const [apiPath, pathItem] of Object.entries(spec.paths || {})) {
    for (const [method, operation] of Object.entries(pathItem)) {
      if (!operation.operationId) continue;

      const requiredRole = operation['x-required-role'];
      if (!requiredRole) {
        console.warn(`⚠️  Skipping ${method.toUpperCase()} ${apiPath}: no x-required-role`);
        continue;
      }

      // Only generate constants for non-read roles
      if (requiredRole === readRole) continue;

      const transformedId = transformOperationId(operation.operationId);
      const hookName = `use${capitalize(transformedId)}`;
      const constantName = hookNameToRoleConstant(hookName);

      roleConstants.push({ constantName, roleValue: requiredRole });
      roleValues.add(requiredRole);

      // Only mutation methods go into the role map
      const isMutation = ['post', 'put', 'patch', 'delete'].includes(method.toLowerCase());
      if (isMutation) {
        roleMap[hookName] = constantName;
      }
    }
  }

  // Build the RequiredRole union from distinct non-read role values
  const unionMembers = Array.from(roleValues).sort().map(v => `"${v}"`).join(' | ');

  // Write roles.ts
  const lines = [
    '// Generated role constants - do not edit manually',
    '',
  ];

  // Sort constants alphabetically for stable output
  const sorted = [...roleConstants].sort((a, b) => a.constantName.localeCompare(b.constantName));
  for (const { constantName, roleValue } of sorted) {
    lines.push(`export const ${constantName} = "${roleValue}" as const;`);
  }

  lines.push('');
  lines.push(`export type RequiredRole = ${unionMembers || 'never'};`);
  lines.push('');

  writeFileSync(ROLES_FILE, lines.join('\n'));
  console.log('✅ Role constants generated');

  // Write role-map.json (sorted keys for stable output)
  const sortedMap = {};
  for (const key of Object.keys(roleMap).sort()) {
    sortedMap[key] = roleMap[key];
  }
  writeFileSync(ROLE_MAP_FILE, JSON.stringify(sortedMap, null, 2) + '\n');
  console.log('✅ Role map generated');
}

/**
 * Main generation function
 */
async function generateAPI(options = {}) {
  const { fetchMode = false, buildMode = false } = options;

  try {
    console.log('🚀 Starting API code generation...');

    // Ensure output directory exists
    ensureOutputDir();

    // Fetch or load OpenAPI spec
    const spec = await fetchAndCache({
      forceRefresh: fetchMode,
      buildMode: buildMode
    });

    // Generate all the files
    generateTypes();
    generateClient();
    generateHooks(spec);
    generateRoles(spec);

    console.log('✅ API code generation completed successfully!');
    console.log(`   Generated files:`);
    console.log(`   - ${TYPES_FILE}`);
    console.log(`   - ${CLIENT_FILE}`);
    console.log(`   - ${HOOKS_FILE}`);
    console.log(`   - ${ROLES_FILE}`);
    console.log(`   - ${ROLE_MAP_FILE}`);

  } catch (error) {
    console.error('❌ API generation failed:', error.message);
    throw error;
  }
}

// CLI usage
if (process.argv[1] === __filename) {
  const args = process.argv.slice(2);
  const fetchMode = args.includes('--fetch');
  const buildMode = args.includes('--cache-only');

  generateAPI({ fetchMode, buildMode })
    .then(() => process.exit(0))
    .catch(() => process.exit(1));
}

export {
  generateAPI,
  generateRoles,
  transformOperationId,
  capitalize,
  hookNameToRoleConstant,
};
