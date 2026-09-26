import type { TSESLint, TSESTree } from '@typescript-eslint/utils';

const messages = {
  routeCall: 'Route interception (`{{ code }}`) is not allowed. Seed backend data or extend test helpers instead of mocking requests.',
  routeHandler: 'Route handler operations (`{{ code }}`) are prohibited. Coordinate backend support rather than faking responses.',
  sseMock: 'SSE mocking (`{{ code }}`) is limited to the sanctioned AI analysis helper. Extend backend fixtures instead.',
  missingDisableReason: 'Include a justification after "--" when disabling testing/no-route-mocks (e.g. "// eslint-disable-next-line testing/no-route-mocks -- reason").',
} as const;

type MessageIds = keyof typeof messages;

type RuleOptions = [];

const ROUTE_METHODS = new Set(['route']);
const ROUTE_HANDLER_METHODS = new Set(['fulfill', 'abort']);
const SSE_METHODS = new Set(['mockSSE', 'mockSSEWithHeartbeat']);

function recordAlias(
  identifier: TSESTree.Identifier | null,
  target: 'route' | 'sse',
  routeIdentifiers: Set<string>,
  sseIdentifiers: Set<string>
) {
  if (!identifier) {
    return;
  }

  if (target === 'route') {
    routeIdentifiers.add(identifier.name);
  } else {
    sseIdentifiers.add(identifier.name);
  }
}

type PatternLike =
  | TSESTree.BindingName
  | TSESTree.AssignmentPattern
  | TSESTree.RestElement;

function extractIdentifier(node: PatternLike): TSESTree.Identifier | null {
  switch (node.type) {
    case 'Identifier':
      return node;
    case 'AssignmentPattern':
      return extractIdentifier(node.left as PatternLike);
    case 'RestElement':
      return extractIdentifier(node.argument as PatternLike);
    case 'ObjectPattern':
    case 'ArrayPattern':
      return null;
    default:
      return null;
  }
}

function visitPattern(
  node: PatternLike,
  routeIdentifiers: Set<string>,
  sseIdentifiers: Set<string>,
  inheritedKey?: string
): void {
  if (node.type === 'Identifier' || node.type === 'AssignmentPattern' || node.type === 'RestElement') {
    const id = extractIdentifier(node);
    if (inheritedKey === 'route') {
      recordAlias(id, 'route', routeIdentifiers, sseIdentifiers);
    } else if (inheritedKey && SSE_METHODS.has(inheritedKey)) {
      recordAlias(id, 'sse', routeIdentifiers, sseIdentifiers);
    }
    if (node.type === 'RestElement') {
      return;
    }
    if (node.type === 'AssignmentPattern') {
      return;
    }
    return;
  }

  if (node.type === 'ObjectPattern') {
    for (const property of node.properties) {
      if (property.type === 'Property') {
        const key =
          property.key.type === 'Identifier' ? property.key.name : undefined;
        visitPattern(property.value as PatternLike, routeIdentifiers, sseIdentifiers, key);
      } else if (property.type === 'RestElement') {
        visitPattern(property.argument as PatternLike, routeIdentifiers, sseIdentifiers, inheritedKey);
      }
    }
    return;
  }

  if (node.type === 'ArrayPattern') {
    for (const element of node.elements) {
      if (!element) {
        continue;
      }
      visitPattern(element as PatternLike, routeIdentifiers, sseIdentifiers, inheritedKey);
    }
  }
}

const noRouteMocksRule: TSESLint.RuleModule<MessageIds, RuleOptions> = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Disallow Playwright route and SSE mocks in favour of real backend flows.',
    },
    schema: [],
    messages,
  },
  defaultOptions: [],
  create(context) {
    const sourceCode = context.sourceCode;
    const routeIdentifiers = new Set<string>(['route']);
    const sseIdentifiers = new Set<string>(['mockSSE', 'mockSSEWithHeartbeat']);

    function report(node: TSESTree.Node, messageId: MessageIds, code: string) {
      context.report({ node, messageId, data: { code } });
    }

    function handleCallExpression(node: TSESTree.CallExpression) {
      const callee = node.callee;

      if (callee.type === 'Identifier') {
        if (routeIdentifiers.has(callee.name)) {
          report(node, 'routeCall', callee.name);
          return;
        }
        if (sseIdentifiers.has(callee.name)) {
          report(node, 'sseMock', callee.name);
          return;
        }
        return;
      }

      if (
        callee.type === 'MemberExpression' &&
        !callee.computed &&
        callee.property.type === 'Identifier'
      ) {
        const propertyName = callee.property.name;
        const calleeText = sourceCode.getText(callee);

        if (ROUTE_METHODS.has(propertyName)) {
          report(node, 'routeCall', calleeText);
          return;
        }

        if (SSE_METHODS.has(propertyName)) {
          report(node, 'sseMock', calleeText);
          return;
        }

        if (ROUTE_HANDLER_METHODS.has(propertyName)) {
          const object = callee.object;
          let matchesRoute = false;

          if (object.type === 'Identifier') {
            matchesRoute = routeIdentifiers.has(object.name);
          } else if (
            object.type === 'MemberExpression' &&
            !object.computed &&
            object.property.type === 'Identifier' &&
            object.property.name === 'route'
          ) {
            matchesRoute = true;
          }

          if (matchesRoute) {
            report(node, 'routeHandler', calleeText);
          }
        }
      }
    }

    function handleVariableDeclarator(node: TSESTree.VariableDeclarator) {
      visitPattern(node.id as PatternLike, routeIdentifiers, sseIdentifiers);

      if (
        node.id.type === 'Identifier' &&
        node.init &&
        node.init.type === 'MemberExpression' &&
        !node.init.computed &&
        node.init.property.type === 'Identifier'
      ) {
        const propName = node.init.property.name;
        if (ROUTE_METHODS.has(propName)) {
          routeIdentifiers.add(node.id.name);
        }
        if (SSE_METHODS.has(propName)) {
          sseIdentifiers.add(node.id.name);
        }
      }
    }

    function handleFunctionParams(params: TSESTree.Parameter[]) {
      for (const param of params) {
        processParameter(param);
      }
    }

    function processParameter(param: TSESTree.Parameter): void {
      switch (param.type) {
        case 'Identifier':
        case 'ObjectPattern':
        case 'ArrayPattern':
          visitPattern(param as PatternLike, routeIdentifiers, sseIdentifiers);
          break;
        case 'AssignmentPattern':
          processParameter(param.left as TSESTree.Parameter);
          break;
        case 'RestElement':
          processParameter(param.argument as TSESTree.Parameter);
          break;
        case 'TSParameterProperty':
          processParameter(param.parameter as TSESTree.Parameter);
          break;
        default:
          break;
      }
    }

    return {
      VariableDeclarator: handleVariableDeclarator,
      CallExpression: handleCallExpression,
      FunctionDeclaration(node) {
        handleFunctionParams(node.params);
      },
      FunctionExpression(node) {
        handleFunctionParams(node.params);
      },
      ArrowFunctionExpression(node) {
        handleFunctionParams(node.params);
      },
      'Program:exit'() {
        for (const comment of sourceCode.getAllComments()) {
          const value = comment.value.trim();
          const match = value.match(
            /^eslint-disable-next-line\s+testing\/no-route-mocks\b(.*)$/
          );
          if (!match) {
            continue;
          }

          const trailing = match[1]?.trim() ?? '';
          if (!/^--\s+.+/.test(trailing)) {
            context.report({ node: comment, messageId: 'missingDisableReason' });
          }
        }
      },
    } satisfies TSESLint.RuleListener;
  },
};

export default noRouteMocksRule;
