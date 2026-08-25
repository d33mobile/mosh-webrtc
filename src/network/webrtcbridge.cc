/*
    Mosh: the mobile shell
    Copyright 2012 Keith Winstein

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see <http://www.gnu.org/licenses/>.

    In addition, as a special exception, the copyright holders give
    permission to link the code of portions of this program with the
    OpenSSL library under certain conditions as described in each
    individual source file, and distribute linked combinations including
    the two.

    You must obey the GNU General Public License in all respects for all
    of the code used other than OpenSSL. If you modify file(s) with this
    exception, you may extend this exception to your version of the
    file(s), but you are not obligated to do so. If you do not wish to do
    so, delete this exception statement from your version. If you delete
    this exception statement from all source files in the program, then
    also delete it here.
*/

#include "src/include/config.h"

#include <cerrno>
#include <chrono>
#include <cstddef>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <stdexcept>
#include <vector>

#include <arpa/inet.h>
#include <poll.h>
#include <unistd.h>

#include <rtc/rtc.hpp>

#include "src/network/webrtcbridge.h"

using namespace Network;

static const char STUN_SERVER_ENV[] = "MOSH_STUN_SERVER";
static const char DEFAULT_STUN_SERVER[] = "stun.l.google.com:19302";
static const char CHANNEL_LABEL[] = "mosh";

/* Largest UDP payload the loopback socket can hand us in one read. */
static const size_t MAX_DATAGRAM_BYTES = 65536;

/* How long the reader thread sleeps in poll() before re-checking the stop flag. */
static const int READER_POLL_MS = 100;

static const char BASE64_ALPHABET[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

static std::string base64_encode( const std::string& in )
{
  std::string out;
  unsigned int bits = 0;
  int nbits = 0;
  for ( std::string::const_iterator it = in.begin(); it != in.end(); ++it ) {
    bits = ( bits << 8 ) | static_cast<unsigned char>( *it );
    nbits += 8;
    while ( nbits >= 6 ) {
      nbits -= 6;
      out.push_back( BASE64_ALPHABET[( bits >> nbits ) & 0x3f] );
    }
  }
  if ( nbits > 0 ) {
    out.push_back( BASE64_ALPHABET[( bits << ( 6 - nbits ) ) & 0x3f] );
  }
  while ( out.size() % 4 != 0 ) {
    out.push_back( '=' );
  }
  return out;
}

static std::string base64_decode( const std::string& in )
{
  std::string out;
  unsigned int bits = 0;
  int nbits = 0;
  for ( std::string::const_iterator it = in.begin(); it != in.end(); ++it ) {
    if ( *it == '=' ) {
      break;
    }
    const char* pos = strchr( BASE64_ALPHABET, *it );
    if ( pos == NULL || *it == '\0' ) {
      throw std::runtime_error( "invalid base64 in SDP" );
    }
    bits = ( bits << 6 ) | static_cast<unsigned int>( pos - BASE64_ALPHABET );
    nbits += 6;
    if ( nbits >= 8 ) {
      nbits -= 8;
      out.push_back( static_cast<char>( ( bits >> nbits ) & 0xff ) );
    }
  }
  return out;
}

static std::string stun_server_from_env( void )
{
  const char* value = getenv( STUN_SERVER_ENV );
  if ( value == NULL || *value == '\0' ) {
    fprintf( stderr, "WebRTCBridge: env %s not set, using default=%s\n", STUN_SERVER_ENV, DEFAULT_STUN_SERVER );
    return DEFAULT_STUN_SERVER;
  }
  fprintf( stderr, "WebRTCBridge: env %s = %s\n", STUN_SERVER_ENV, value );
  return value;
}

WebRTCBridge::WebRTCBridge( bool s_offerer )
  : offerer( s_offerer ), pc(), dc(), sock( -1 ), peer(), peer_known( false ), mutex(), cond(),
    gathering_complete( false ), channel_open( false ), reader(), stop_reader( false )
{
  sock = socket( AF_INET, SOCK_DGRAM, 0 );
  if ( sock < 0 ) {
    throw std::runtime_error( std::string( "socket: " ) + strerror( errno ) );
  }
  /* The server forks the user's shell; do not leak the socket into it. */
  fcntl( sock, F_SETFD, FD_CLOEXEC );
  struct sockaddr_in local;
  memset( &local, 0, sizeof local );
  local.sin_family = AF_INET;
  local.sin_addr.s_addr = htonl( INADDR_LOOPBACK );
  local.sin_port = 0;
  if ( bind( sock, reinterpret_cast<struct sockaddr*>( &local ), sizeof local ) < 0 ) {
    throw std::runtime_error( std::string( "bind: " ) + strerror( errno ) );
  }

  rtc::Configuration config;
  config.iceServers.emplace_back( "stun:" + stun_server_from_env() );
  pc = std::make_shared<rtc::PeerConnection>( config );

  pc->onGatheringStateChange( [this]( rtc::PeerConnection::GatheringState state ) {
    if ( state == rtc::PeerConnection::GatheringState::Complete ) {
      std::lock_guard<std::mutex> lock( mutex );
      gathering_complete = true;
      cond.notify_all();
    }
  } );
  pc->onLocalCandidate( [this]( rtc::Candidate candidate ) {
    fprintf( stderr, "WebRTCBridge: local candidate %s\n", std::string( candidate ).c_str() );
  } );
  pc->onStateChange( []( rtc::PeerConnection::State state ) {
    if ( state == rtc::PeerConnection::State::Failed ) {
      fputs( "WebRTCBridge: peer connection failed\n", stderr );
    }
  } );

  if ( offerer ) {
    rtc::DataChannelInit init;
    init.reliability.unordered = true;
    init.reliability.maxRetransmits = 0;
    /* createDataChannel() also triggers setLocalDescription() and gathering. */
    attach_channel( pc->createDataChannel( CHANNEL_LABEL, init ) );
  } else {
    pc->onDataChannel( [this]( std::shared_ptr<rtc::DataChannel> channel ) { attach_channel( channel ); } );
  }
}

WebRTCBridge::~WebRTCBridge()
{
  {
    std::lock_guard<std::mutex> lock( mutex );
    stop_reader = true;
  }
  if ( reader.joinable() ) {
    reader.join();
  }
  if ( dc ) {
    dc->close();
  }
  if ( pc ) {
    pc->close();
  }
  if ( sock >= 0 ) {
    close( sock );
  }
}

void WebRTCBridge::attach_channel( std::shared_ptr<rtc::DataChannel> channel )
{
  dc = channel;
  dc->onOpen( [this]() {
    std::lock_guard<std::mutex> lock( mutex );
    channel_open = true;
    cond.notify_all();
  } );
  dc->onClosed( [this]() {
    std::lock_guard<std::mutex> lock( mutex );
    channel_open = false;
    cond.notify_all();
  } );
  dc->onMessage(
    [this]( rtc::binary data ) { deliver( reinterpret_cast<const unsigned char*>( data.data() ), data.size() ); },
    []( std::string ) {} );
}

std::string WebRTCBridge::local_description( void )
{
  {
    std::unique_lock<std::mutex> lock( mutex );
    cond.wait( lock, [this] { return gathering_complete; } );
  }
  auto description = pc->localDescription();
  if ( !description ) {
    throw std::runtime_error( "no local description after gathering" );
  }
  return base64_encode( std::string( *description ) );
}

void WebRTCBridge::set_remote_description( const std::string& base64_sdp )
{
  /* The SDP carries no type; each side knows what the other must have sent. */
  pc->setRemoteDescription( rtc::Description( base64_decode( base64_sdp ), offerer ? "answer" : "offer" ) );
}

bool WebRTCBridge::wait_open( unsigned int ms )
{
  std::unique_lock<std::mutex> lock( mutex );
  return cond.wait_for( lock, std::chrono::milliseconds( ms ), [this] { return channel_open; } );
}

std::string WebRTCBridge::port( void ) const
{
  struct sockaddr_in local;
  socklen_t len = sizeof local;
  if ( getsockname( sock, reinterpret_cast<struct sockaddr*>( &local ), &len ) < 0 ) {
    throw std::runtime_error( std::string( "getsockname: " ) + strerror( errno ) );
  }
  return std::to_string( ntohs( local.sin_port ) );
}

void WebRTCBridge::set_peer( const struct sockaddr* addr, socklen_t len )
{
  if ( addr->sa_family != AF_INET || len < sizeof peer ) {
    throw std::runtime_error( "WebRTCBridge peer must be IPv4" );
  }
  std::lock_guard<std::mutex> lock( mutex );
  memcpy( &peer, addr, sizeof peer );
  peer_known = true;
}

void WebRTCBridge::start( void )
{
  reader = std::thread( &WebRTCBridge::reader_loop, this );
}

std::string WebRTCBridge::selected_candidate_pair( void )
{
  rtc::Candidate local, remote;
  if ( !pc->getSelectedCandidatePair( &local, &remote ) ) {
    return "none";
  }
  return std::string( local ) + " <-> " + std::string( remote );
}

void WebRTCBridge::reader_loop( void )
{
  std::vector<unsigned char> buf( MAX_DATAGRAM_BYTES );
  struct pollfd pfd;
  pfd.fd = sock;
  pfd.events = POLLIN;
  for ( ;; ) {
    {
      std::lock_guard<std::mutex> lock( mutex );
      if ( stop_reader ) {
        return;
      }
    }
    pfd.revents = 0;
    if ( poll( &pfd, 1, READER_POLL_MS ) <= 0 ) {
      continue;
    }
    struct sockaddr_in from;
    socklen_t from_len = sizeof from;
    ssize_t n = recvfrom( sock, buf.data(), buf.size(), 0, reinterpret_cast<struct sockaddr*>( &from ), &from_len );
    if ( n < 0 ) {
      if ( errno == EINTR || errno == EAGAIN ) {
        continue;
      }
      fprintf( stderr, "WebRTCBridge: recvfrom: %s\n", strerror( errno ) );
      return;
    }
    {
      /* The mosh side may hop ports; whoever spoke last is the peer. */
      std::lock_guard<std::mutex> lock( mutex );
      peer = from;
      peer_known = true;
      if ( !channel_open ) {
        continue;
      }
    }
    try {
      dc->send( reinterpret_cast<const std::byte*>( buf.data() ), static_cast<size_t>( n ) );
    } catch ( const std::exception& e ) {
      fprintf( stderr, "WebRTCBridge: send: %s\n", e.what() );
    }
  }
}

void WebRTCBridge::deliver( const unsigned char* data, size_t len )
{
  struct sockaddr_in to;
  {
    std::lock_guard<std::mutex> lock( mutex );
    if ( !peer_known ) {
      return;
    }
    to = peer;
  }
  if ( sendto( sock, data, len, 0, reinterpret_cast<const struct sockaddr*>( &to ), sizeof to ) < 0 ) {
    fprintf( stderr, "WebRTCBridge: sendto: %s\n", strerror( errno ) );
  }
}
