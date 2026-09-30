import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default [
  { ignores: ['dist'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    settings: { react: { version: '18.3' } },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...react.configs['jsx-runtime'].rules,
      ...reactHooks.configs.recommended.rules,
      // react-hooks 7 ships the React Compiler lint set. This rule flags the
      // "reset state when a dep changes" pattern used across the pages. It is a
      // perf hint, not a bug; keep it visible as a warning until those effects
      // are reworked.
      'react-hooks/set-state-in-effect': 'warn',
      'react/jsx-no-target-blank': 'off',
      // Plain JS project, no TypeScript — prop-types would be pure noise here.
      'react/prop-types': 'off',
      'react/no-unescaped-entities': 'off',
      // Deliberate best-effort try/catch (localStorage, stream parsing) is a pattern here.
      'no-empty': ['error', { allowEmptyCatch: true }],
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
    },
  },
]
