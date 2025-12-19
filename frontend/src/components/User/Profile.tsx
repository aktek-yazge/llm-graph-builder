import { useMemo, useRef, useState } from 'react';
import { Menu, Typography, IconButton, Avatar } from '@neo4j-ndl/react';
import { ChevronDownIconOutline } from '@neo4j-ndl/react/icons';
import { useAuth } from '../../context/AuthContext';
import { useNavigate } from 'react-router-dom';
import { SKIP_AUTH } from '../../utils/Constants';

export default function Profile() {
  const [showMenu, setShowOpen] = useState<boolean>(false);
  const iconbtnRef = useRef<HTMLButtonElement | null>(null);
  const navigate = useNavigate();

  // Use JWT auth context (only when not skipping auth)
  const auth = SKIP_AUTH ? null : useAuth();
  const user = auth?.user;
  const isAuthenticated = auth?.isAuthenticated ?? false;
  const isLoading = auth?.isLoading ?? false;
  const logout = auth?.logout;

  const settings = useMemo(
    () => [
      {
        title: 'Logout',
        onClick: async () => {
          if (logout) {
            await logout();
          }
          navigate('/login');
        },
      },
    ],
    [logout, navigate]
  );

  const handleClick = () => {
    setShowOpen(true);
  };

  const handleClose = () => {
    setShowOpen(false);
  };

  // Skip auth mode - no profile
  if (SKIP_AUTH) {
    return null;
  }

  if (isLoading) {
    return <Avatar />;
  }

  if (isAuthenticated && user) {
    return (
      <div className='p-1.5 h-12 profile-container'>
        <>
          <Avatar
            className='md:flex hidden'
            name={user.username?.charAt(0).toUpperCase()}
            shape='square'
            size='large'
            type='letters'
          />
          <div className='flex flex-col'>
            <Typography variant='body-medium' className='p-0.5'>
              {user.username ?? 'User'}
            </Typography>

            <Typography variant='body-small' className='p-0.5'>
              {user.email ?? ''}
            </Typography>

            <Menu className='mt-1.5 ml-4' anchorRef={iconbtnRef} isOpen={showMenu} onClose={handleClose}>
              <Menu.Items>
                {settings.map((setting) => (
                  <Menu.Item key={setting.title} onClick={() => setting.onClick()} title={setting.title} />
                ))}
              </Menu.Items>
            </Menu>
          </div>
          <IconButton ref={iconbtnRef} ariaLabel='settings' isClean onClick={handleClick}>
            <ChevronDownIconOutline />
          </IconButton>
        </>
      </div>
    );
  }

  return null;
}
