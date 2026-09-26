const ROUTE_METHODS = new Set(['route']);
const ROUTE_HANDLER_METHODS = new Set(['fulfill', 'abort']);
const SSE_METHODS = new Set(['mockSSE', 'mockSSEWithHeartbeat']);

const messages = {
  routeCall:
    'Route interception (`{{ code }}`) is not allowed. Seed backend data or extend test helpers instead of mocking requests.',
  routeHandler:
    'Route handler operations (`{{ code }}`) are prohibited. Coordinate backend support rather than faking responses.',
  sseMock:
    'SSE mocking (`{{ code }}`) is limited to the sanctioned AI analysis helper. Extend backend fixtures instead.',
  missingDisableReason:
    'Include a justification after "--" when disabling testing/no-route-mocks (e.g. "// eslint-disable-next-line testing/no-route-mocks -- reason").',
};

const recordAlias = (identifier, target, routeIdentifiers, sseIdentifiers) => {
  if (!identifier) {
    return;
  }

  if (target === 'route') {
    routeIdentifiers.add(identifier.name);
  } else {
    sseIdentifiers.add(identifier.name);
  }
};

const extractIdentifier = (node) => {
  switch (node.type) {
    case 'Identifier':
      return node;
    case 'AssignmentPattern':
      return extractIdentifier(node.left);
    case 'RestElement':
      return extractIdentifier(node.argument);
    case 'ObjectPattern':
    case 'ArrayPattern':
      return null;
    default:
      return null;
  }
};

const visitPattern = (node, routeIdentifiers, sseIdentifiers, inheritedKey) => {
  if (
    node.type === 'Identifier' ||
    node.type === 'AssignmentPattern' ||
    node.type === 'RestElement'
  ) {
    const id = extractIdentifier(node);
    if (inheritedKey === 'route') {
      recordAlias(id, 'route', routeIdentifiers, sseIdentifiers);
    } else if (inheritedKey && SSE_METHODS.has(inheritedKey)) {
      recordAlias(id, 'sse', routeIdentifiers, sseIdentifiers);
    }
    return;
  }

  if (node.type === 'ObjectPattern') {
    for (const property of node.properties) {
      if (property.type === 'Property') {
        const key = property.key.type === 'Identifier' ? property.key.name : undefined;
        visitPattern(property.value, routeIdentifiers, sseIdentifiers, key);
      } else if (property.type === 'RestElement') {
        visitPattern(property.argument, routeIdentifiers, sseIdentifiers, inheritedKey);
      }
    }
    return;
  }

  if (node.type === 'ArrayPattern') {
    for (const element of node.elements) {
      if (!element) {
        continue;
      }
      visitPattern(element, routeIdentifiers, sseIdentifiers, inheritedKey);
    }
  }
};

const processParameter = (param, routeIdentifiers, sseIdentifiers) => {
  switch (param.type) {
    case 'Identifier':
    case 'ObjectPattern':
    case 'ArrayPattern':
      visitPattern(param, routeIdentifiers, sseIdentifiers);
      break;
    case 'AssignmentPattern':
      processParameter(param.left, routeIdentifiers, sseIdentifiers);
      break;
    case 'RestElement':
      processParameter(param.argument, routeIdentifiers, sseIdentifiers);
      break;
    case 'TSParameterProperty':
      processParameter(param.parameter, routeIdentifiers, sseIdentifiers);
      break;
    default:
      break;
  }
};

const noRouteMocksRule = {
  meta: {
    type: 'problem',
    docs: {
      description: 'Disallow Playwright route and SSE mocks in favour of real backend flows.',
    },
    schema: [],
    messages,
  },
  defaultOptions: [],
  create(context) {
    const sourceCode = context.sourceCode;
    const routeIdentifiers = new Set(['route']);
    const sseIdentifiers = new Set(['mockSSE', 'mockSSEWithHeartbeat']);

    const report = (node, messageId, code) => {
      context.report({ node, messageId, data: { code } });
    };

    const handleCallExpression = (node) => {
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
    };

    const handleVariableDeclarator = (node) => {
      visitPattern(node.id, routeIdentifiers, sseIdentifiers);

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
    };

    const handleFunctionParams = (params) => {
      for (const param of params) {
        processParameter(param, routeIdentifiers, sseIdentifiers);
      }
    };

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
            /^eslint-disable-next-line\s+testing\/no-route-mocks\b(.*)$/,
          );
          if (!match) {
            continue;
          }

          const trailing = (match[1] ?? '').trim();
          if (!/^--\s+.+/.test(trailing)) {
            context.report({ node: comment, messageId: 'missingDisableReason' });
          }
        }
      },
    };
  },
};

export default noRouteMocksRule;
